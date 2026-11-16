#!/usr/bin/env python3
"""HermesEnabler Ultra 1 / watchOS 26.6 + 26.6.1 — NEW activation-path experiment.

Runs our already-tested V1 patch first, then changes the activation backend:
 * permitted direct MG reads no longer force an MCM sandbox escape;
 * exact-file Class-13 queries try bounded 23U67/23U95 flag alternatives;
 * a token is accepted ONLY if the existing protected file opens O_RDWR;
 * exhausted tests are negative-cached to prevent runaway retry loops;
 * firmware-matching MG metadata is preserved; no hardcoded offsets;
 * the Watch exposes explicit one-touch testing and log export.

A built IPA is NOT evidence of writable system access or native face activation.
"""
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve()
runpy.run_path(str(Path(__file__).with_name("patch_ultra26.py")), run_name="__main__")

def change(path, old, new, label):
    p = root / path
    s = p.read_text(encoding="utf-8")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"V2 {label}: expected 1 anchor in {path}, got {count}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print(f"V2 PATCH OK: {label}")

m = "HermesEnabler2/BQCore.m"

# The 1.6.2 activation engine performs an MCM escape merely to READ the MG
# cache. That masks usable read access and prevents reaching the actual write
# capability check. Reuse the same binary-plist validation as our V1 backup.
change(m,
"""NSMutableDictionary *BQLoadGestalt(NSString **err) {
    NSString *link = nil;
    int64_t h = BQEscape(kMGCaches, &link);""",
"""NSMutableDictionary *BQLoadGestalt(NSString **err) {
    NSData *directData = [NSData dataWithContentsOfFile:BQMobileGestaltPath()];
    if (bq_valid_gestalt_data(directData)) {
        NSMutableDictionary *direct = [[NSPropertyListSerialization
            propertyListWithData:directData
                         options:NSPropertyListMutableContainersAndLeaves
                          format:NULL error:NULL] mutableCopy];
        if (direct) {
            static BOOL once = NO;
            if (!once) {
                once = YES;
                BQLog(@"ULTRA26: activation can read MG directly, even without MCM; %lu bytes",
                      (unsigned long)directData.length);
            }
            return direct;
        }
    }
    // Keep the unmodified upstream fallback when a direct read is unavailable.
    NSString *link = nil;
    int64_t h = BQEscape(kMGCaches, &link);""",
"activation reads MobileGestalt directly before MCM")

# Implement a candidate not exercised in the user's earlier log: exact-file
# part-domain queries. Other 23U95 researchers reported low-bit flag tokens,
# but those were read-only. We specifically reject any token that cannot open
# O_RDWR; this does not modify the protected file.
insert = r'''
// Test-build V2 only. watchOS 26.6 / 26.6.1 exact-file candidate.
// No writes are made here. Never treat token=YES as writable permission.
static BOOL gUltra26ExactCandidateTried = NO;
static BOOL gUltra26NormalCandidateFailed = NO;
static BOOL gUltra26WritableCandidateSeen = NO;
static NSUInteger gUltra26LastTrials = 0;
static NSUInteger gUltra26LastTokens = 0;
static NSUInteger gUltra26LastWritable = 0;

static BOOL bq_ultra26_build(void) {
    NSString *v = [WKInterfaceDevice currentDevice].systemVersion;
    return [v isEqualToString:@"26.6"] || [v isEqualToString:@"26.6.1"];
}

static int64_t bq_ultra26_exact_file_escape(NSString **linkOut) {
    gUltra26LastTrials = 0;
    gUltra26LastTokens = 0;
    gUltra26LastWritable = 0;

    NSString *full = [kMGCaches stringByAppendingPathComponent:@"com.apple.MobileGestalt.plist"];
    NSString *shortPath = [full hasPrefix:@"/private/var/"]
        ? [@"/var/" stringByAppendingString:[full substringFromIndex:[@"/private/var/" length]]]
        : full;
    // Three low-bit flags are informed by physical 23U95 testing. The two
    // high-bit flags are controls already tried in upstream bad_query.
    static const uint64_t flags[] = {
        0x800000000ULL, 0x810000000ULL, 0x900000000ULL,
        0x8000000000ULL, 0x8100000000ULL
    };

    for (NSString *file in @[shortPath, full]) {
        NSString *dom = [bq_dots(8) stringByAppendingString:file];
        for (NSUInteger i = 0; i < sizeof(flags) / sizeof(flags[0]); ++i) {
            ++gUltra26LastTrials;
            NSString *label = [NSString stringWithFormat:@"ULTRA26-exact-file/0x%llx/%@",
                               (unsigned long long)flags[i],
                               [file hasPrefix:@"/private/"] ? @"private-var" : @"var"];
            NSString *resultPath = nil;
            int64_t handle = bq_query_escape(13, kMGGroup, YES, 3, dom, flags[i],
                                              label, &resultPath);
            if (handle < 0) continue;
            ++gUltra26LastTokens;

            errno = 0;
            int fd = open(file.fileSystemRepresentation, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
            int writeError = fd < 0 ? errno : 0;
            BOOL canWrite = fd >= 0;
            if (fd >= 0) close(fd);
            BQLog(@"ULTRA26 exact-file token=%lld flags=0x%llx writable=%d errno=%d (%s)",
                  (long long)handle, (unsigned long long)flags[i],
                  canWrite, writeError, writeError ? strerror(writeError) : "allowed");

            if (canWrite) {
                ++gUltra26LastWritable;
                gUltra26WritableCandidateSeen = YES;
                if (linkOut) *linkOut = kMGCaches;
                BQLog(@"ULTRA26 WRITABLE CANDIDATE: exact MG file open(O_RDWR) succeeded; no bytes changed");
                return handle;
            }
            // Token without write access cannot unlock any native face.
            if (g.release) g.release(handle);
        }
    }
    BQLog(@"ULTRA26 exact-file probe done: trials=%lu tokens=%lu writable=%lu",
          (unsigned long)gUltra26LastTrials,
          (unsigned long)gUltra26LastTokens,
          (unsigned long)gUltra26LastWritable);
    return -5;
}

// Invoked manually from the Watch diagnostics UI. Safe: no system writes.
NSString *BQProbeWatch26WritePaths(void) {
    if (!BQCoreInit()) return @"MCM initialization failed";
    if (!bq_ultra26_build()) return @"This probe is limited to watchOS 26.6 / 26.6.1";
    NSString *link = nil;
    int64_t handle = bq_ultra26_exact_file_escape(&link);
    gUltra26ExactCandidateTried = YES;
    if (handle >= 0) {
        if (g.release) g.release(handle);
        gUltra26NormalCandidateFailed = NO;
    }
    NSString *answer = [NSString stringWithFormat:
        @"Exact-file MCM tests: %lu\nTokens acquired: %lu\nWritable tokens: %lu\n"
         "MG write permission: %@\n(Protected file was NOT modified.)",
        (unsigned long)gUltra26LastTrials,
        (unsigned long)gUltra26LastTokens,
        (unsigned long)gUltra26LastWritable,
        gUltra26LastWritable ? @"AVAILABLE — activation may be tested"
                            : @"DENIED — do not attempt activation"];
    BQLog(@"ULTRA26 PRE-FLIGHT: %@", answer);
    return answer;
}
'''
change(m,
"""int64_t BQEscape(NSString *target, NSString **linkOut) {""",
insert + """
int64_t BQEscape(NSString *target, NSString **linkOut) {""",
"add bounded exact-file token candidate and user-triggered preflight")

change(m,
"""    bq_load_mcm();
    int64_t last = -1;

    // 1. Old bad_query:""",
"""    bq_load_mcm();
    int64_t last = -1;

    // watchOS 26: first test the EXACT file, rejecting read-only tokens.
    // Avoid the previous 52x repeated failing query sequences.
    BOOL ultra26MG = bq_ultra26_build() && [target isEqualToString:kMGCaches];
    if (ultra26MG) {
        if (gUltra26NormalCandidateFailed && !gUltra26WritableCandidateSeen) {
            BQLog(@"ULTRA26: previous full escape failed; no repeated unsafe queries");
            return -5;
        }
        if (!gUltra26ExactCandidateTried || gUltra26WritableCandidateSeen) {
            NSString *exactPath = nil;
            int64_t exact = bq_ultra26_exact_file_escape(&exactPath);
            gUltra26ExactCandidateTried = YES;
            if (exact >= 0) {
                if (linkOut) *linkOut = exactPath ?: kMGCaches;
                return exact;
            }
        }
    }

    // 1. Old bad_query:""",
"integrate exact-file test as first activation escape route with negative caching")

change(m,
"""    BQLog(@"escape: all strategies failed (last=%lld) target=%@", (long long)last, target);
    return last;""",
"""    if (ultra26MG) gUltra26NormalCandidateFailed = YES;
    BQLog(@"escape: all strategies failed (last=%lld) target=%@", (long long)last, target);
    return last;""",
"cache failed 26.6/26.6.1 activation attempts")

# The original hardcoded class13 BAD-QUERY branch is attempted only while
# gTraversalState >= 0. The explicit preflight resets nothing and retains
# the original branch for a one-time fallback, with logs at the first try.

# Preserve original dynamic firmware layout and DeviceBrand checks; do not
# apply historical hardcoded offsets from the separate research fork.
change("HermesEnabler2/BQCore.h",
"""NSString *BQProbeGestaltWriteAccess(void);""",
"""NSString *BQProbeGestaltWriteAccess(void);
NSString *BQProbeWatch26WritePaths(void);""",
"declare watchOS 26 native activation preflight")

# Allow the user to run one targeted probe and inspect actual token/write
# counters, without relying on automatic UI refreshes.
view = "HermesEnabler2/BackupView.swift"
change(view,
"""    @State private var writeAccessResult: String?
    @State private var logTransferResult: String?""",
"""    @State private var writeAccessResult: String?
    @State private var logTransferResult: String?
    @State private var watch26PreflightResult: String?""",
"add explicit Watch native activation preflight state")
change(view,
"""                Button("Check Direct Write Access") {
                    writeAccessResult = BQProbeGestaltWriteAccess()
                }""",
"""                Button("Probe WatchOS 26 Writable Token") {
                    watch26PreflightResult = BQProbeWatch26WritePaths()
                }
                if let watch26PreflightResult {
                    Text(watch26PreflightResult).font(.footnote.monospaced())
                }
                Button("Check Direct Write Access") {
                    writeAccessResult = BQProbeGestaltWriteAccess()
                }""",
"add on-watch original exact-file token/writability probe")

print("V2 READY: direct activation read, exact-file candidate, preflight, negative cache.")
