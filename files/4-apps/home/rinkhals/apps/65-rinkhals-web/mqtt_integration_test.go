package main

import (
	"io"
	"net"
	"net/url"
	"os"
	"sync"
	"testing"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

// Opt in with an isolated, anonymous broker bound to loopback, for example:
// RINKHALS_TEST_MQTT_BROKER=tcp://127.0.0.1:18883 go test -race -run '^TestMQTTDiscoveryIntegration$' -v
// The TCP proxy injects a publisher-only connection failure; all MQTT packets
// are handled by the real broker and the production Paho discovery callback.
func TestMQTTDiscoveryIntegration(t *testing.T) {
	broker := os.Getenv("RINKHALS_TEST_MQTT_BROKER")
	if broker == "" {
		t.Skip("set RINKHALS_TEST_MQTT_BROKER to an isolated loopback MQTT broker")
	}
	u, err := url.Parse(broker)
	if err != nil || u.Scheme != "tcp" || u.Port() == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		t.Fatal("RINKHALS_TEST_MQTT_BROKER must be tcp://<loopback-address>:<port>")
	}
	host := u.Hostname()
	if ip := net.ParseIP(host); host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		t.Fatal("integration tests only connect to loopback brokers, never a printer or remote broker")
	}
	identity := "rk-test-" + randomToken()[:12]
	topic := "rinkhals/integration/" + identity + "/discovery"
	payload := `{"device":{"ids":"` + identity + `"},"components":{"memory_usage":{"platform":"sensor"}},"state_topic":"rinkhals/integration/state"}`

	newClient := func(address, suffix string) mqtt.Client {
		opts := mqtt.NewClientOptions().AddBroker(address).SetClientID(identity + suffix)
		opts.SetConnectTimeout(2 * time.Second)
		opts.SetWriteTimeout(2 * time.Second)
		opts.SetAutoReconnect(false)
		client := mqtt.NewClient(opts)
		t.Cleanup(func() { client.Disconnect(100) })
		awaitMQTTToken(t, "connect "+suffix, client.Connect())
		return client
	}
	observer := newClient(broker, "-obs")
	// Registered before the publisher cleanup, so the publisher has stopped
	// before the retained integration-only record is removed.
	t.Cleanup(func() {
		token := observer.Publish(topic, 1, true, []byte{})
		if !token.WaitTimeout(3*time.Second) || token.Error() != nil {
			t.Errorf("could not remove integration retained record: %v", token.Error())
		}
	})
	messages := make(chan integrationMQTTMessage, 16)
	awaitMQTTToken(t, "subscribe observer", observer.Subscribe(topic, 1, func(_ mqtt.Client, msg mqtt.Message) {
		select {
		case messages <- integrationMQTTMessage{payload: string(msg.Payload()), retained: msg.Retained()}:
		default:
		}
	}))

	proxy, err := newMQTTFaultProxy(u.Host)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(proxy.close)
	connected, lost := make(chan struct{}, 8), make(chan struct{}, 8)
	opts := mqtt.NewClientOptions().AddBroker("tcp://" + proxy.listener.Addr().String()).SetClientID(identity + "-pub")
	opts.SetConnectTimeout(time.Second)
	opts.SetWriteTimeout(2 * time.Second)
	opts.SetMaxReconnectInterval(250 * time.Millisecond)
	opts.SetAutoReconnect(true)
	publishDiscovery := discoveryPublisher(topic, payload, identity)
	opts.OnConnect = func(client mqtt.Client) {
		publishDiscovery(client)
		select {
		case connected <- struct{}{}:
		default:
		}
	}
	opts.OnConnectionLost = func(mqtt.Client, error) {
		select {
		case lost <- struct{}{}:
		default:
		}
	}
	publisher := mqtt.NewClient(opts)
	t.Cleanup(func() { publisher.Disconnect(100) })
	awaitMQTTToken(t, "connect publisher", publisher.Connect())
	awaitMQTTEvent(t, "initial discovery callback", connected)
	if msg := awaitMQTTMessage(t, "initial discovery", messages); msg.payload != payload {
		t.Fatalf("initial discovery payload = %q, want %q", msg.payload, payload)
	}
	assertRetained := func(suffix string) {
		client := newClient(broker, suffix)
		retained := make(chan integrationMQTTMessage, 1)
		awaitMQTTToken(t, "subscribe retained probe", client.Subscribe(topic, 1, func(_ mqtt.Client, msg mqtt.Message) {
			select {
			case retained <- integrationMQTTMessage{payload: string(msg.Payload()), retained: msg.Retained()}:
			default:
			}
		}))
		msg := awaitMQTTMessage(t, "retained discovery "+suffix, retained)
		if !msg.retained || msg.payload != payload {
			t.Fatalf("retained discovery %s: retained=%t payload=%q", suffix, msg.retained, msg.payload)
		}
		client.Disconnect(100)
	}
	assertRetained("-first")
	t.Log("initial discovery is available to a new subscriber as a retained message")

	proxy.pause()
	awaitMQTTEvent(t, "unexpected publisher disconnection", lost)
	// Simulate loss of the broker's retained discovery while the publisher
	// cannot reconnect. The observer stays connected directly to the broker.
	awaitMQTTToken(t, "clear retained discovery", observer.Publish(topic, 1, true, []byte{}))
	if msg := awaitMQTTMessage(t, "retained record deletion", messages); msg.payload != "" {
		t.Fatalf("expected retained deletion, got %q", msg.payload)
	}
	proxy.resume()
	awaitMQTTEvent(t, "automatic reconnect discovery callback", connected)
	if msg := awaitMQTTMessage(t, "discovery after reconnect", messages); msg.payload != payload {
		t.Fatalf("reconnect discovery payload = %q, want %q", msg.payload, payload)
	}
	assertRetained("-second")
	t.Log("automatic Paho reconnect republished discovery and restored the retained message")
}

type integrationMQTTMessage struct {
	payload  string
	retained bool
}

func awaitMQTTToken(t *testing.T, operation string, token mqtt.Token) {
	t.Helper()
	if !token.WaitTimeout(15 * time.Second) {
		t.Fatalf("%s timed out", operation)
	}
	if err := token.Error(); err != nil {
		t.Fatalf("%s: %v", operation, err)
	}
}

func awaitMQTTEvent(t *testing.T, operation string, events <-chan struct{}) {
	t.Helper()
	select {
	case <-events:
	case <-time.After(15 * time.Second):
		t.Fatalf("%s timed out", operation)
	}
}

func awaitMQTTMessage(t *testing.T, operation string, messages <-chan integrationMQTTMessage) integrationMQTTMessage {
	t.Helper()
	select {
	case msg := <-messages:
		return msg
	case <-time.After(15 * time.Second):
		t.Fatalf("%s timed out", operation)
		return integrationMQTTMessage{}
	}
}

type mqttFaultProxy struct {
	listener net.Listener
	broker   string
	mu       sync.Mutex
	paused   bool
	closed   bool
	active   map[net.Conn]net.Conn
	workers  sync.WaitGroup
}

func newMQTTFaultProxy(broker string) (*mqttFaultProxy, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	p := &mqttFaultProxy{listener: listener, broker: broker, active: make(map[net.Conn]net.Conn)}
	p.workers.Add(1)
	go func() {
		defer p.workers.Done()
		for {
			client, err := listener.Accept()
			if err != nil {
				return
			}
			p.mu.Lock()
			reject := p.paused || p.closed
			p.mu.Unlock()
			if reject {
				client.Close()
				continue
			}
			upstream, err := net.DialTimeout("tcp", p.broker, time.Second)
			if err != nil {
				client.Close()
				continue
			}
			p.mu.Lock()
			if p.paused || p.closed {
				p.mu.Unlock()
				client.Close()
				upstream.Close()
				continue
			}
			p.active[client] = upstream
			p.workers.Add(1)
			p.mu.Unlock()
			go p.forward(client, upstream)
		}
	}()
	return p, nil
}

func (p *mqttFaultProxy) forward(client, upstream net.Conn) {
	defer p.workers.Done()
	done := make(chan struct{}, 2)
	copyStream := func(dst, src net.Conn) {
		io.Copy(dst, src)
		done <- struct{}{}
	}
	go copyStream(upstream, client)
	go copyStream(client, upstream)
	<-done
	client.Close()
	upstream.Close()
	<-done
	p.mu.Lock()
	delete(p.active, client)
	p.mu.Unlock()
}

func (p *mqttFaultProxy) pause() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.paused = true
	for client, upstream := range p.active {
		client.Close()
		upstream.Close()
	}
}

func (p *mqttFaultProxy) resume() {
	p.mu.Lock()
	p.paused = false
	p.mu.Unlock()
}

func (p *mqttFaultProxy) close() {
	p.mu.Lock()
	p.closed = true
	p.mu.Unlock()
	p.listener.Close()
	p.pause()
	p.workers.Wait()
}
