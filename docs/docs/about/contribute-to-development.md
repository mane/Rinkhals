---
title: Contribute to development
weight: 1
---

Here are some aggregated answer I gave on Discord. If you want to now more, join the server and ask me!

Also, have a look at the other pages, such as [File Structure - Kobra Startup Sequence](../firmware/file-structure.md#kobra-startup-sequence)


## Checks before merging

PR checks run the Python/shell regressions, frontend type checks and build,
Chromium browser tests, Go race tests and vet, MQTT reconnect integration,
and a static Linux ARMv7 backend build. Browser requests use fixture APIs;
the MQTT test uses a disposable broker on loopback. These tests do not connect
to or operate a printer.

For a complete firmware build, run the **Build SWU** workflow on the branch
being reviewed. Its `clean_build` option disables cached layers. The workflow
validates all model-specific update, installer and tools archives before
uploading them. The release workflows run the same validation before publishing.
Local equivalents from the repository root are:

```sh
docker build --output type=local,dest=./build/dist .
python3 build/verify-swu.py build/dist
```

With the `just` environment configured, `just swu-all` also validates its
outputs; `just swu-verify` checks existing outputs. Validation checks archive
integrity and required runtime files without executing the update payloads.

Run the browser tests locally after installing dependencies:

```sh
cd web-portal
npm ci
npm run build
npx playwright install chromium
npm run test:browser
```

The optional Go MQTT integration test requires an isolated broker. From the
repository root, start the same broker used in CI:

```sh
docker run --detach --name rinkhals-mqtt-test \
  --publish 127.0.0.1:18883:1883 \
  --mount type=bind,source="$PWD/tests/integration/mosquitto.conf",target=/mosquitto/config/mosquitto.conf,readonly \
  eclipse-mosquitto:2
```

After building and copying the frontend into the backend's `ui` directory,
run this from `files/4-apps/home/rinkhals/apps/65-rinkhals-web`:

```sh
RINKHALS_TEST_MQTT_BROKER=tcp://127.0.0.1:18883 go test -race ./...
```

Remove the disposable broker when finished: `docker rm -f rinkhals-mqtt-test`.

Before releasing changes to OTA or hardware integrations, also record manual
results on a test printer: model and stock firmware version, successful and
failed updates, contention between update entry points, Moonraker restart,
ACE unload/eject behavior, LVGL modal navigation, and Home Assistant discovery.
Package and mocked API tests cannot establish those hardware results.
Track outstanding hardware checks in an issue when merging reviewed changes;
merging the code does not establish that the firmware is ready for release.

## Questions and Answers

### What if I want learn more? Klipper etc. I need to learn Python?

For Rinkhals itself you don't need Klipper, you'll need Linux in general (filesystem, shell scripts, ...), Docker for easy setup, Python for Moonraker, the UI and other tools
Depends on what you're interested in!

For Klipper you have two parts, Klippy in Python on the SBC / or GoKlipper in Go for Anycubic, and Klipper for the MCU in C / C++

### What would you recommend as topics for this kind of firmware structure? Or what topics / documentation would help understand how you built Rinkhals to work along side the stock firmware so well?

I'd say the basics of this project is a good understanding of the basics of Linux.
It's not only about pure dev, but about putting scripts and configuration together to build the system.

There are many different approaches to firmware dev, you could rebuild everything and flash your own images like you would download an .img for a Raspberry Pi for example.
For Rinkhals I decided to create an overlay because it's way safer, easier to build and it allows me to rely on existing software from Anycubic.

So have a look at [https://github.com/rinkhals-community/Rinkhals/blob/master/files/3-rinkhals/start.sh](https://github.com/rinkhals-community/Rinkhals/blob/master/files/3-rinkhals/start.sh), it uses mount heavily to "hide" the stock filesystem and overlay my own to add binaries, scripts and all.

I really think those printers are super interesting to dev on, be curious about how things are starting, why you hear a beep when you plug a USB drive in. For example for startup follow `/etc/init.d/rkS`, after many scripts it will lead you to understand how Rinkhals starts.
Look at the stock firmware images as well, it's interesting to see how they built that, and you will realize it's far from perfect!
Every time I ask myself such a question and I explore, I end up learning a ton about Linux and how I could modify things.

The dev part is useful to automate things, build some programs that were missing. For example I added a touch UI in Python, a system monitor in Go, and a lot of Shell script programming.

The last part is binary hacking, maybe the most complex but I use Ghidra and BinaryNinja to analyse Anycubic binaries and patch them to get the behavior I want.

And TBH I don't know much about Klipper, 3D printing in general, but it's interesting to dig into that and learn along the way.
Almost everything is now open/broken for our printers, have a look and try to understand, RTFM for weird linux commands, and ask for leads here, I'd be happy to provide pointers!
