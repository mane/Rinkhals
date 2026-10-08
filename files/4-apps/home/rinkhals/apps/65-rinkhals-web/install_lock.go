package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const installStageLockPath = "/tmp/rinkhals-update.lock"
const installStageTokenEnv = "RINKHALS_UPDATE_LOCK_TOKEN"

// Shared with tools.sh and the touch UI. Atomic mkdir serializes every user of
// /useremain/update_swu, including processes outside this web server. Tokens let
// child installers inherit ownership without removing their parent's lock.
type installStageLock struct {
	path  string
	token string
}

func acquireInstallStage() (*installStageLock, error) {
	return acquireInstallStageAt(installStageLockPath)
}

func acquireInstallStageAt(path string) (*installStageLock, error) {
	if err := os.Mkdir(path, 0700); err != nil {
		return nil, fmt.Errorf("install staging is locked; another update or maintenance operation may be running: %w", err)
	}
	lock := &installStageLock{path: path, token: randomToken()}
	if err := os.WriteFile(filepath.Join(path, "owner"), []byte(lock.token+"\n"), 0600); err != nil {
		os.Remove(path)
		return nil, err
	}
	return lock, nil
}

func (lock *installStageLock) release() {
	owner, err := os.ReadFile(filepath.Join(lock.path, "owner"))
	if err == nil && strings.TrimSpace(string(owner)) == lock.token {
		os.Remove(filepath.Join(lock.path, "owner"))
		os.Remove(lock.path)
	}
}

func (lock *installStageLock) environment() []string {
	env := make([]string, 0, len(os.Environ())+1)
	for _, value := range os.Environ() {
		if !strings.HasPrefix(value, installStageTokenEnv+"=") {
			env = append(env, value)
		}
	}
	return append(env, installStageTokenEnv+"="+lock.token)
}
