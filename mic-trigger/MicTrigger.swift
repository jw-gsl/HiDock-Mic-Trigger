import Foundation
import CoreAudio

// Force line-buffered stdout so output reaches the parent app via pipe
setlinebuf(stdout)

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

func readDeviceRunningSomewhere(_ deviceID: AudioDeviceID) -> Bool? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var running: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &running)
    guard status == noErr else { return nil }
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

// MARK: - HiDock device discovery

/// Find the first audio input device whose name starts with "HiDock".
func findHiDockDeviceName() -> String? {
    for dev in getAllAudioDevices() {
        guard deviceHasInput(dev) else { continue }
        if let name = getDeviceName(dev), name.hasPrefix("HiDock") {
            return name
        }
    }
    return nil
}

// MARK: - ffmpeg helpers

/// Kill any orphaned ffmpeg processes that are holding a HiDock audio input.
/// This prevents stale processes from blocking new trigger sessions.
func killOrphanedFFmpeg(hidockDevice: String) {
    let pipe = Pipe()
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/ps")
    p.arguments = ["-eo", "pid,args"]
    p.standardOutput = pipe
    p.standardError = Pipe()
    do {
        try p.run()
        p.waitUntilExit()
    } catch { return }

    let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    // Match both old numeric-index style and new device-name style
    let needles = [
        "ffmpeg -loglevel error -f avfoundation -i :\(hidockDevice)",
        "ffmpeg -loglevel error -f avfoundation -i :1",  // legacy default
    ]
    let myPid = ProcessInfo.processInfo.processIdentifier
    for line in output.components(separatedBy: "\n") {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard needles.contains(where: { trimmed.contains($0) }) else { continue }
        let parts = trimmed.split(separator: " ", maxSplits: 1)
        guard let pid = Int32(parts.first ?? "") else { continue }
        if pid != myPid {
            print("Killing orphaned ffmpeg (pid \(pid)).")
            kill(pid, SIGTERM)
        }
    }
}

final class FFmpegHolder {
    private var proc: Process?

    func start(ffmpegPath: String, hidockDevice: String) {
        guard proc == nil else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: ffmpegPath)
        p.arguments = [
            "-loglevel", "error",
            "-f", "avfoundation",
            "-i", ":\(hidockDevice)",
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

// MARK: - Argument parsing

func parseArgs() -> (micName: String, hidockDevice: String?) {
    let args = CommandLine.arguments
    var micName = "Samson Q2U Microphone"
    var hidockDevice: String? = nil

    var i = 1
    while i < args.count {
        switch args[i] {
        case "--mic":
            i += 1
            if i < args.count { micName = args[i] }
        case "--hidock":
            i += 1
            if i < args.count { hidockDevice = args[i] }
        case "--audio-index":
            // Legacy flag — ignored (auto-detect is now used)
            i += 1
        case "--list-inputs":
            for dev in getAllAudioDevices() {
                guard deviceHasInput(dev) else { continue }
                if let name = getDeviceName(dev) {
                    print(name)
                }
            }
            exit(0)
        default:
            break
        }
        i += 1
    }
    return (micName, hidockDevice)
}

// MARK: - Main

let config = parseArgs()
let usbMicName = config.micName

let ffmpegPath = "/opt/homebrew/bin/ffmpeg"

guard FileManager.default.isExecutableFile(atPath: ffmpegPath) else {
    print("ffmpeg not found at \(ffmpegPath). Update ffmpegPath to `which ffmpeg`.")
    exit(1)
}

guard let foundUSBID = findInputDeviceID(named: usbMicName) else {
    print("Could not find USB mic input device named '\(usbMicName)'. Check the name.")
    exit(1)
}
var usbID = foundUSBID

// Resolve HiDock device name for ffmpeg (auto-detect or use --hidock override)
let hidockDevice: String
if let override = config.hidockDevice {
    hidockDevice = override
} else if let detected = findHiDockDeviceName() {
    hidockDevice = detected
} else {
    print("Could not find a HiDock audio input device. Is the HiDock connected?")
    exit(1)
}

print("Found USB mic '\(usbMicName)' (deviceID \(usbID)).")
print("Using HiDock audio device: \(hidockDevice)")

let holder = FFmpegHolder()

// Kill any orphaned ffmpeg from a previous crashed/restarted session
killOrphanedFFmpeg(hidockDevice: hidockDevice)

// Debounce: require state to be stable for this many samples
let pollInterval: TimeInterval = 0.25
let debounceSamples = 4  // 4 * 0.25s = 1s

let initialState = readDeviceRunningSomewhere(usbID) ?? false
var lastState = initialState
var stableCount = 0
var failedReadCount = 0

print("Initial USB mic in-use state: \(initialState ? "IN USE" : "NOT IN USE")")

// Important: if mic is already in-use when app starts, begin holding immediately.
if initialState {
    print("Initial state already IN USE → holding HiDock mic open.")
    holder.start(ffmpegPath: ffmpegPath, hidockDevice: hidockDevice)
}

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

// Poll on a timer so the main run loop stays free for signal handling
let pollTimer = DispatchSource.makeTimerSource(queue: .global(qos: .userInitiated))
pollTimer.schedule(deadline: .now(), repeating: pollInterval)
pollTimer.setEventHandler {
    guard let current = readDeviceRunningSomewhere(usbID) else {
        failedReadCount += 1
        if failedReadCount % 8 == 0 {
            if let refreshedID = findInputDeviceID(named: usbMicName), refreshedID != usbID {
                print("Trigger mic deviceID changed \(usbID) -> \(refreshedID); continuing monitoring.")
                usbID = refreshedID
                failedReadCount = 0
            }
        }
        return
    }

    failedReadCount = 0

    if current == lastState {
        // Reconcile if we somehow missed a transition.
        if current && !holder.isRunning() {
            print("Reconciled IN USE state with holder stopped → holding HiDock mic open.")
            holder.start(ffmpegPath: ffmpegPath, hidockDevice: hidockDevice)
        } else if !current && holder.isRunning() {
            print("Reconciled NOT IN USE state with holder running → releasing HiDock mic.")
            holder.stop()
        }
        stableCount = 0
        return
    }

    stableCount += 1
    if stableCount >= debounceSamples {
        lastState = current
        stableCount = 0
        if current {
            print("USB mic became IN USE → holding HiDock mic open.")
            holder.start(ffmpegPath: ffmpegPath, hidockDevice: hidockDevice)
        } else {
            print("USB mic became NOT IN USE → releasing HiDock mic.")
            holder.stop()
        }
    }
}
pollTimer.resume()
dispatchMain()
