import Foundation
import CoreAudio

// MARK: - CoreAudio helpers

func getAllAudioDevices() -> [AudioDeviceID] {
    var propsize: UInt32 = 0
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var status = AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propsize)
    guard status == noErr else { return [] }

    let count = Int(propsize) / MemoryLayout<AudioDeviceID>.size
    var deviceIDs = Array(repeating: AudioDeviceID(0), count: count)
    status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propsize, &deviceIDs)
    guard status == noErr else { return [] }
    return deviceIDs
}

func getDeviceName(_ deviceID: AudioDeviceID) -> String? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var cfName: CFString = "" as CFString
    var size = UInt32(MemoryLayout<CFString>.size)
    let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &cfName)
    guard status == noErr else { return nil }
    return cfName as String
}

func deviceHasInput(_ deviceID: AudioDeviceID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreamConfiguration,
        mScope: kAudioDevicePropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain
    )
    var propsize: UInt32 = 0
    var status = AudioObjectGetPropertyDataSize(deviceID, &address, 0, nil, &propsize)
    guard status == noErr else { return false }

    let bufferListPtr = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: Int(propsize))
    defer { bufferListPtr.deallocate() }

    status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &propsize, bufferListPtr)
    guard status == noErr else { return false }

    let bufferList = UnsafeMutableAudioBufferListPointer(bufferListPtr)
    let channels = bufferList.reduce(0) { $0 + Int($1.mNumberChannels) }
    return channels > 0
}

func isDeviceRunningSomewhere(_ deviceID: AudioDeviceID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var running: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &running)
    guard status == noErr else { return false }
    return running != 0
}

func findInputDeviceID(named targetName: String) -> AudioDeviceID? {
    for dev in getAllAudioDevices() {
        guard deviceHasInput(dev) else { continue }
        if let name = getDeviceName(dev), name == targetName {
            return dev
        }
    }
    return nil
}

// MARK: - ffmpeg helpers

final class FFmpegHolder {
    private var proc: Process?

    func start(ffmpegPath: String, audioIndex: Int) {
        guard proc == nil else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: ffmpegPath)
        p.arguments = [
            "-loglevel", "error",
            "-f", "avfoundation",
            "-i", ":\(audioIndex)",
            "-ac", "1",
            "-ar", "48000",
            "-f", "null",
            "-"
        ]
        p.standardInput = Pipe()
        p.standardOutput = Pipe()
        p.standardError = Pipe()

        do {
            try p.run()
            proc = p
            print("Started holding HiDock input (ffmpeg pid \(p.processIdentifier)).")
        } catch {
            print("Failed to start ffmpeg: \(error)")
        }
    }

    func stop() {
        guard let p = proc else { return }
        p.terminate()
        proc = nil
        print("Stopped holding HiDock input.")
    }

    func isRunning() -> Bool {
        return proc != nil
    }
}

// MARK: - Main

// Device names from your ffmpeg list
let usbMicName = "Samson Q2U Microphone"
let hiDockAudioIndex = 1

let ffmpegPath = "/opt/homebrew/bin/ffmpeg"

guard FileManager.default.isExecutableFile(atPath: ffmpegPath) else {
    print("ffmpeg not found at \(ffmpegPath). Update ffmpegPath to `which ffmpeg`.")
    exit(1)
}

guard let usbID = findInputDeviceID(named: usbMicName) else {
    print("Could not find USB mic input device named '\(usbMicName)'. Check the name.")
    exit(1)
}

print("Found USB mic '\(usbMicName)' (deviceID \(usbID)).")
print("Using HiDock AVFoundation audio index: \(hiDockAudioIndex)")

let holder = FFmpegHolder()

// Debounce: require state to be stable for this many samples
let pollInterval: TimeInterval = 0.25
let debounceSamples = 4  // 4 * 0.25s = 1s

var lastState = isDeviceRunningSomewhere(usbID)
var stableCount = 0

print("Initial USB mic in-use state: \(lastState ? "IN USE" : "NOT IN USE")")

// Handle SIGINT/SIGTERM to stop ffmpeg cleanly
signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sigintSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
let sigtermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
let shutdownHandler = {
    if holder.isRunning() { holder.stop() }
    exit(0)
}
sigintSource.setEventHandler(handler: shutdownHandler)
sigtermSource.setEventHandler(handler: shutdownHandler)
sigintSource.resume()
sigtermSource.resume()

while true {
    let current = isDeviceRunningSomewhere(usbID)
    if current == lastState {
        stableCount = 0
    } else {
        stableCount += 1
        if stableCount >= debounceSamples {
            lastState = current
            stableCount = 0
            if current {
                print("USB mic became IN USE → holding HiDock mic open.")
                holder.start(ffmpegPath: ffmpegPath, audioIndex: hiDockAudioIndex)
            } else {
                print("USB mic became NOT IN USE → releasing HiDock mic.")
                holder.stop()
            }
        }
    }
    Thread.sleep(forTimeInterval: pollInterval)
}
