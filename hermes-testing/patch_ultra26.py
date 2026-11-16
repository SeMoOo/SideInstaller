#!/usr/bin/env python3
"""Patch a pinned upstream HermesEnabler 1.6.2 checkout for Ultra 1 testing.

Scope: PR #89 read-first backups, safe write-open diagnostics, and Watch->iPhone
bqtool.log export. Keep upstream activation routines unchanged. This is NOT a
verified native Hermès face unlock for watchOS 26.6/26.6.1.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

def replace_once(relative, old, new, label):
    path = root / relative
    current = path.read_text(encoding="utf-8")
    n = current.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one exact match in {relative}, got {n}")
    path.write_text(current.replace(old, new, 1), encoding="utf-8")
    print(f"PATCH OK: {label} ({relative})")

# 1) Read-first MobileGestalt backup: upstream PR #89, function-local replacement.
m = "HermesEnabler2/BQCore.m"
old = """    NSString *link = nil;
    int64_t h = BQEscape(kMGCaches, &link);
    if (h < 0) {
        if (err) *err = bq_escape_error(h);
        return nil;
    }
    NSData *data = [NSData dataWithContentsOfFile:[link stringByAppendingPathComponent:@"com.apple.MobileGestalt.plist"]];
    BQEndEscape(h);
    if (!bq_valid_gestalt_data(data)) {
        if (err) *err = NSLocalizedString(@"MobileGestalt plist is missing or invalid.", nil);
        return nil;
    }
"""
new = """    // PR #89: a read-only backup may work even when MCM denies an extension.
    NSData *data = [NSData dataWithContentsOfFile:BQMobileGestaltPath()];
    if (bq_valid_gestalt_data(data)) {
        BQLog(@"MG direct read succeeded: %lu bytes (no protected write)", (unsigned long)data.length);
    } else {
        BQLog(@"MG direct read unavailable; trying MCM read fallback");
        NSString *link = nil;
        int64_t h = BQEscape(kMGCaches, &link);
        if (h < 0) {
            if (err) *err = bq_escape_error(h);
            return nil;
        }
        data = [NSData dataWithContentsOfFile:[link stringByAppendingPathComponent:@"com.apple.MobileGestalt.plist"]];
        BQEndEscape(h);
        if (!bq_valid_gestalt_data(data)) {
            if (err) *err = NSLocalizedString(@"MobileGestalt plist is missing or invalid.", nil);
            return nil;
        }
    }
"""
replace_once(m, old, new, "read-first MG backup")

replace_once("HermesEnabler2/BQCore.h",
             "BOOL BQBackupExists(void);\n",
             "BOOL BQBackupExists(void);\nNSString *BQProbeGestaltWriteAccess(void);\n",
             "expose safe direct write-open diagnostic")

replace_once(m,
"""BOOL BQInPlaceWrite(void) {""",
"""// Probe permissions only: O_NOFOLLOW prevents symlinks; no create, truncate or write.
NSString *BQProbeGestaltWriteAccess(void) {
    NSString *path = BQMobileGestaltPath();
    int fd = open(path.fileSystemRepresentation, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    int writeError = fd < 0 ? errno : 0;
    if (fd >= 0) close(fd);

    fd = open(path.fileSystemRepresentation, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    int readWriteError = fd < 0 ? errno : 0;
    if (fd >= 0) close(fd);

    NSString *result = [NSString stringWithFormat:@"O_WRONLY: %s (errno %d)\\nO_RDWR: %s (errno %d)",
                        writeError ? strerror(writeError) : "allowed", writeError,
                        readWriteError ? strerror(readWriteError) : "allowed", readWriteError];
    BQLog(@"MG write-open diagnostic (no bytes written): %@", result);
    return result;
}

BOOL BQInPlaceWrite(void) {""", "non-writing MG write permission probe")

# 2) PR #89: do not gate initial/manual backup on successful MCM setup.
replace_once("HermesEnabler2/ContentModel.swift",
"""        if !BQCoreInit() {
            toggleFailed = shouldAlertOnFailure""",
"""        let requiresMCM: Bool
        switch action {
        case .backup, .manualBackup: requiresMCM = false
        default: requiresMCM = true
        }
        if requiresMCM && !BQCoreInit() {
            toggleFailed = shouldAlertOnFailure""",
"allow read-first backups without MCM init")

# 3) Watch view: expose write-access result and one-touch log transfer.
view = "HermesEnabler2/BackupView.swift"
replace_once(view,
"""    let model: ContentModel

    var body: some View {""",
"""    let model: ContentModel
    @State private var writeAccessResult: String?
    @State private var logTransferResult: String?

    var body: some View {""",
"Watch diagnostic state")
replace_once(view,
"""            // Backup library is always visible,""",
"""            Section("Ultra 1 / watchOS 26 Diagnostics") {
                Text("Checks do not modify the system MobileGestalt cache.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button("Check Direct Write Access") {
                    writeAccessResult = BQProbeGestaltWriteAccess()
                }
                if let writeAccessResult {
                    Text(writeAccessResult).font(.footnote.monospaced())
                }
                Button("Send bqtool.log to iPhone") {
                    logTransferResult = WatchConnectivityManager.shared.transferDiagnosticLog()
                        ? "Log queued for transfer. Open the iPhone companion app."
                        : "Transfer not available. Check the Watch app connection and retry."
                }
                if let logTransferResult {
                    Text(logTransferResult).font(.footnote)
                }
            }

            // Backup library is always visible,""",
"Watch diagnostics interface")

# 4) Transfer the real Watch bqtool.log over existing WatchConnectivity link.
watch_mgr = "HermesEnabler2/WatchConnectivity/WatchConnectivityManager.swift"
p = root / watch_mgr
src = p.read_text(encoding="utf-8")
if "func transferDiagnosticLog()" in src:
    raise RuntimeError("Watch log transfer already exists")
src += """
// Test-build-only extension. The iPhone handles the log as text, never as a backup.
extension WatchConnectivityManager {
    func transferDiagnosticLog() -> Bool {
        let session = WCSession.default
        guard session.activationState == .activated else {
            print("[WC] Cannot transfer Watch diagnostic log: session not activated")
            return false
        }
        print("[WC] Watch diagnostic log export requested")
        let sourceURL = URL(fileURLWithPath: BQLogPath())
        guard FileManager.default.fileExists(atPath: sourceURL.path) else {
            print("[WC] Watch diagnostic log not found")
            return false
        }
        let metadata: [String: Any] = [
            "type": "watchDiagnosticLog",
            "watchOSVersion": WKInterfaceDevice.current().systemVersion,
            "transferDate": Date().timeIntervalSince1970
        ]
        let transfer = session.transferFile(sourceURL, metadata: metadata)
        print("[WC] bqtool.log transfer queued: \\(transfer.isTransferring)")
        return true
    }
}
"""
p.write_text(src, encoding="utf-8")
print("PATCH OK: WatchConnectivity diagnostic log transfer")

# 5) iPhone companion receives Watch log and offers share sheet (Files/AirDrop).
iphone_mgr = "HermesCompanion/WatchConnectivity/WatchConnectivityManager.swift"
replace_once(iphone_mgr,
"""    @Published var lastVerificationMessage = ""

    private var session:""",
"""    @Published var lastVerificationMessage = ""
    @Published var latestDiagnosticLogURL: URL?

    private var session:""",
"iPhone diagnostic log state")

replace_once(iphone_mgr,
"""    ) {
        print("[WC] Backup file received")

        // WCSession removes its temporary file after this delegate returns.""",
"""    ) {
        // Keep logs separate from the MobileGestalt plist validator/backup history.
        if file.metadata?["type"] as? String == "watchDiagnosticLog" {
            do {
                let data = try Data(contentsOf: file.fileURL)
                guard !data.isEmpty, data.count < 5_000_000 else {
                    print("[WC] Invalid/oversized diagnostic log")
                    return
                }
                Task { @MainActor in
                    do {
                        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                            .appendingPathComponent("WatchDiagnostics", isDirectory: true)
                        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                        let url = dir.appendingPathComponent("bqtool-\\(Int(Date().timeIntervalSince1970)).log")
                        try data.write(to: url, options: .atomic)
                        self.latestDiagnosticLogURL = url
                        print("[WC] Watch diagnostic log saved: \\(url.path)")
                    } catch {
                        print("[WC] Failed to store Watch diagnostic log: \\(error)")
                    }
                }
            } catch {
                print("[WC] Failed to receive Watch diagnostic log: \\(error)")
            }
            return
        }
        print("[WC] Backup file received")

        // WCSession removes its temporary file after this delegate returns.""",
"iPhone receives and stores real Watch diagnostic logs")

iphone_view = "HermesCompanion/ContentView.swift"
replace_once(iphone_view,
"""            Button {
                watchManager.loadBackupHistory()
                showingHistory = true""",
"""            Button {
                if let url = watchManager.latestDiagnosticLogURL {
                    shareURL = ShareItem(url: url)
                } else {
                    showMessage("No Watch log received. Tap Send bqtool.log to iPhone on the Watch.")
                }
            } label: {
                Label("Export Watch Diagnostic Log", systemImage: "doc.text")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 5)
            }
            .buttonStyle(.bordered)
            .disabled(watchManager.latestDiagnosticLogURL == nil)

            Button {
                watchManager.loadBackupHistory()
                showingHistory = true""",
"iPhone share sheet for diagnostic log")

print("ALL PATCHES APPLIED — native Hermès activation engine unchanged and unverified.")
