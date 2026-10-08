package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

func TestSaveFileRejectsInvalidRequestsWithoutTruncating(t *testing.T) {
	path := filepath.Join(t.TempDir(), "printer.cfg")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, content := range []interface{}{123, nil} {
		body, _ := json.Marshal(map[string]interface{}{"path": path, "content": content})
		w := httptest.NewRecorder()
		handleSaveFile(w, httptest.NewRequest(http.MethodPost, "/api/saveFile", bytes.NewReader(body)))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("invalid content %v: status %d", content, w.Code)
		}
		data, _ := os.ReadFile(path)
		if string(data) != "original" {
			t.Fatal("invalid request changed the destination")
		}
	}
	body, _ := json.Marshal(map[string]string{"path": path, "content": "replacement"})
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		w := httptest.NewRecorder()
		handleSaveFile(w, httptest.NewRequest(method, "/api/saveFile", bytes.NewReader(body)))
		if w.Code != http.StatusMethodNotAllowed {
			t.Fatalf("method %s: status %d", method, w.Code)
		}
	}
	w := httptest.NewRecorder()
	handleSaveFile(w, httptest.NewRequest(http.MethodPost, "/api/saveFile", strings.NewReader(string(body)+"{}")))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("trailing JSON: status %d", w.Code)
	}
	data, _ := os.ReadFile(path)
	if string(data) != "original" {
		t.Fatal("rejected request changed destination")
	}
}

func TestSaveFileReportsFailureAndPreservesSymlinkAndMode(t *testing.T) {
	dir := t.TempDir()
	body, _ := json.Marshal(map[string]string{"path": filepath.Join(dir, "missing", "file"), "content": "text"})
	w := httptest.NewRecorder()
	handleSaveFile(w, httptest.NewRequest(http.MethodPost, "/api/saveFile", bytes.NewReader(body)))
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("failed write reported status %d", w.Code)
	}
	target := filepath.Join(dir, "config")
	link := filepath.Join(dir, "link")
	if err := os.WriteFile(target, []byte("old"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("config", link); err != nil {
		t.Fatal(err)
	}
	if err := saveTextFile(link, []byte("new")); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(target)
	info, _ := os.Stat(target)
	linkInfo, _ := os.Lstat(link)
	if string(data) != "new" || info.Mode().Perm() != 0600 || linkInfo.Mode()&os.ModeSymlink == 0 {
		t.Fatal("save did not preserve destination contents, mode, or symlink")
	}
}

func TestRinkhalsVersionComparison(t *testing.T) {
	for _, tc := range []struct {
		a, b  string
		newer bool
	}{
		{"20260601_02", "20260601_01", true},
		{"20260601_01", "20260601_02", false},
		{"20260602_01", "20260601_99", true},
		{"20260601_01", "20260601_01_test_webui", false},
		{"20260601_02", "20260601_01_test_webui", true},
		{"2.7.2.7", "2.7.2.1", true},
		{"2.9", "2.10", false},
	} {
		if got := compareVersionsDesc(tc.a, tc.b); got != tc.newer {
			t.Errorf("newer(%q, %q) = %t, want %t", tc.a, tc.b, got, tc.newer)
		}
	}
}

func TestAnycubicAssetURLRequiresDomainBoundary(t *testing.T) {
	for _, tc := range []struct {
		url     string
		allowed bool
	}{
		{"https://cloud-universe.anycubic.com/update.swu", true},
		{"https://cdn.cloud-universe.anycubic.com/update.swu", true},
		{"https://CDN.CLOUD-UNIVERSE.ANYCUBIC.COM/update.swu", true},
		{"https://evilcloud-universe.anycubic.com/update.swu", false},
		{"https://cloud-universe.anycubic.com.attacker.example/update.swu", false},
		{"https://cdn.cloud-universe.anycubic.com@attacker.example/update.swu", false},
		{"http://cdn.cloud-universe.anycubic.com/update.swu", false},
	} {
		if got := validateAssetURL("anycubic", tc.url) == nil; got != tc.allowed {
			t.Errorf("asset URL %q allowed=%t, want %t", tc.url, got, tc.allowed)
		}
	}
}

func TestUpdateScriptPropagatesInstallerFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "update.sh")
	// Rinkhals's actual async wrapper contract: without the argument, the
	// parent exits zero even when the background installer fails.
	script := `#!/bin/sh
if [ "$1" != "async" ]; then
    nohup "$0" async >/dev/null &
    exit 0
fi
exit 7
`
	if err := os.WriteFile(path, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	if err := executeUpdateScript(path, "rinkhals", os.Environ(), io.Discard); err == nil {
		t.Fatal("Rinkhals installer failure was hidden by the async wrapper")
	}
	if err := os.WriteFile(path, []byte("#!/bin/sh\n[ \"$#\" -eq 0 ]\n"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := executeUpdateScript(path, "anycubic", os.Environ(), io.Discard); err != nil {
		t.Fatalf("stock installer received unexpected arguments: %v", err)
	}
}

func TestInstallStageExcludesOtherOperationsAndPreservesOwner(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lock")
	first, err := acquireInstallStageAt(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.release()
	if _, err := acquireInstallStageAt(path); err == nil {
		t.Fatal("another operation acquired the shared stage")
	}
	wrong := &installStageLock{path: path, token: "not-the-owner"}
	wrong.release()
	if _, err := os.Stat(filepath.Join(path, "owner")); err != nil {
		t.Fatal("non-owner removed the lock")
	}
	t.Setenv(installStageTokenEnv, "inherited-old-token")
	count := 0
	for _, entry := range first.environment() {
		if strings.HasPrefix(entry, installStageTokenEnv+"=") {
			count++
			if entry != installStageTokenEnv+"="+first.token {
				t.Fatal("child inherited the wrong token")
			}
		}
	}
	if count != 1 {
		t.Fatalf("child token environment has %d entries", count)
	}
	first.release()
	second, err := acquireInstallStageAt(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.release()
	first.release()
	if _, err := os.Stat(filepath.Join(path, "owner")); err != nil {
		t.Fatal("old owner removed a newer operation's lock")
	}
}

func TestFirmwareSupportUsesBootWhitelist(t *testing.T) {
	script := filepath.Join(t.TempDir(), "tools.sh")
	// Historical patches may exist for 2.4.0.4, but only the boot whitelist
	// decides support. The helper must receive the requested model and version.
	if err := os.WriteFile(script, []byte(`is_supported_firmware() {
    if [ "$1" = K3 ] && [ "$2" = 2.4.6.7 ]; then echo 1; else echo 0; fi
}
`), 0600); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		model, version string
		want           bool
	}{
		{"K3", "2.4.6.7", true}, {"K3", "2.4.0.4", false},
		{"K3", "2.4.6", false}, {"KS1", "2.4.6.7", false},
	} {
		got, err := querySupportedFirmware(script, tc.model, tc.version)
		if err != nil || got != tc.want {
			t.Errorf("support(%s, %s) = %t, %v", tc.model, tc.version, got, err)
		}
	}
	if _, err := querySupportedFirmware(script+"-missing", "K3", "2.4.6.7"); err == nil {
		t.Fatal("missing whitelist did not fail verification")
	}
}

func TestInstallSnapshotAndConcurrentLogs(t *testing.T) {
	firmwareInstallMu.Lock()
	installLog = nil
	installCurrent = &installRunState{State: installStateDownload, Message: "before"}
	firmwareInstallMu.Unlock()
	t.Cleanup(func() {
		firmwareInstallMu.Lock()
		installCurrent = nil
		installLog = nil
		firmwareInstallMu.Unlock()
	})
	snapshot := installSnapshot()
	firmwareInstallMu.Lock()
	setInstallState(func(s *installRunState) { s.Message = "after" })
	firmwareInstallMu.Unlock()
	if snapshot["run"].(installRunState).Message != "before" {
		t.Fatal("snapshot shares mutable run state")
	}
	var wg sync.WaitGroup
	for worker := 0; worker < 3; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for n := 0; n < 300; n++ {
				recordInstallLog("line")
				json.Marshal(installSnapshot())
				firmwareInstallMu.Lock()
				setInstallState(func(s *installRunState) { s.DownloadPct = n })
				firmwareInstallMu.Unlock()
			}
		}()
	}
	wg.Wait()
	if got := len(installSnapshot()["log"].([]string)); got != installLogMax {
		t.Fatalf("log ring contains %d entries, want %d", got, installLogMax)
	}
}

type printerTransport func(*http.Request) (*http.Response, error)

func (f printerTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestPrinterBusyRecheckedAtCommitAndExecution(t *testing.T) {
	original := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = original })
	http.DefaultTransport = printerTransport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"result":{"status":{"print_stats":{"state":"printing"}}}}`)), Header: make(http.Header)}, nil
	})
	w := httptest.NewRecorder()
	handleInstallCommit(w, httptest.NewRequest(http.MethodPost, "/api/firmware/install/commit", strings.NewReader(`{"install_id":"previously-minted"}`)))
	if w.Code != http.StatusConflict || !strings.Contains(w.Body.String(), "busy") {
		t.Fatalf("commit did not reject a new print: %d %s", w.Code, w.Body.String())
	}
	if err := runUpdateScript("rinkhals", nil); err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("execution did not reject a new print: %v", err)
	}
}

func TestForeignOriginsCannotReachTerminalOrMutations(t *testing.T) {
	for _, tc := range []struct {
		origin  string
		allowed bool
	}{
		{"", true}, {"http://printer.local:8090", true},
		{"http://localhost:5173", true}, {"http://attacker.local", false},
		{"http://printer.local:8090.attacker.local", false},
		// HTTPS may terminate at a reverse proxy preserving the public Host.
		{"https://printer.local:8090", true},
		{"ftp://printer.local:8090", false},
	} {
		r := httptest.NewRequest(http.MethodPost, "http://printer.local:8090/api/fs", nil)
		r.Header.Set("Origin", tc.origin)
		if got := allowedWebSocketOrigin(r); got != tc.allowed {
			t.Errorf("origin %q accepted=%t", tc.origin, got)
		}
		called := false
		h := corsMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true }))
		h.ServeHTTP(httptest.NewRecorder(), r)
		if called != tc.allowed {
			t.Errorf("mutation origin %q executed=%t", tc.origin, called)
		}
	}
}

type discoveryToken struct {
	mqtt.Token
	err error
}

func (t discoveryToken) WaitTimeout(time.Duration) bool { return true }
func (t discoveryToken) Error() error                   { return t.err }

type discoveryClient struct {
	mqtt.Client
	calls    int
	err      error
	topic    string
	retained bool
}

func (c *discoveryClient) Publish(topic string, qos byte, retained bool, payload interface{}) mqtt.Token {
	c.calls++
	c.topic, c.retained = topic, retained
	return discoveryToken{err: c.err}
}

func TestDiscoveryPublishesOnEachConnectionAndReportsFailure(t *testing.T) {
	var output bytes.Buffer
	original := log.Writer()
	log.SetOutput(&output)
	t.Cleanup(func() { log.SetOutput(original) })
	client := &discoveryClient{}
	onConnect := discoveryPublisher("homeassistant/device/test/config", "{}", "test")
	if client.calls != 0 {
		t.Fatal("discovery published before a connection")
	}
	onConnect(client)
	onConnect(client)
	if client.calls != 2 || !client.retained || client.topic != "homeassistant/device/test/config" {
		t.Fatal("discovery was not republished on reconnection")
	}
	output.Reset()
	client.err = errors.New("disconnected")
	onConnect(client)
	if !strings.Contains(output.String(), "publish failed") || strings.Contains(output.String(), "Published Home Assistant") {
		t.Fatalf("failed discovery logged as success: %s", output.String())
	}
}
