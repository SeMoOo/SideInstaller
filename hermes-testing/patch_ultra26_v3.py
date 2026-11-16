#!/usr/bin/env python3
"""Research V3: verify whether an MCM-returned path differs from canonical access.

Built on V2. For watchOS 26.6 and 26.6.1, inspect the *actual* Class-13
container_object_get_path() path with open(O_RDWR|O_NOFOLLOW), not just the
canonical path. Only accept it when fstat matches the canonical cache file
(dev, inode, size and regular-file type). No actual writes occur in the probe.

If and ONLY if a writable path is found, route the existing verified,
same-size pwrite/readback/rollback operation through that exact path. No
rename, symlink creation, truncation, or file creation on protected storage.
Does not bypass a read-only sandbox extension or make any unlock claim.
"""
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve()
runpy.run_path(str(Path(__file__).with_name("patch_ultra26_v2.py")), run_name="__main__")

def replace_once(relative, old, new, label):
    p = root / relative
    s = p.read_text(encoding="utf-8")
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"V3 {label}: expected exactly one match in {relative}; got {n}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print(f"V3 PATCH OK: {label}")

m = "HermesEnabler2/BQCore.m"
replace_once(m,
"""static NSUInteger gUltra26LastWritable = 0;

static BOOL bq_ultra26_build(void) {""",
"""static NSUInteger gUltra26LastWritable = 0;
// Set only after a live writable descriptor is verified against the canonical
// protected file's device/inode. Never derive this from a token alone.
static NSString *gUltra26ActiveMGPath = nil;

static BOOL bq_ultra26_build(void) {""",
"track the verified file path (not a token assumption)")

# Distinguish canonical-path access from MCM's original returned traversal path.
# This is a deterministic, bounded test; it cannot make a read-only token write.
replace_once(m,
"""static int64_t bq_ultra26_exact_file_escape(NSString **linkOut) {""",
"""// Test a candidate WITHOUT writing, truncating or creating anything.
// Reject any path that opens a different file or an unverified file type.
static BOOL bq_ultra26_verified_writable_path(NSString *candidate,
                                              NSString *canonical,
                                              NSString *origin) {
    if (!candidate.length || ![candidate hasSuffix:@"/com.apple.MobileGestalt.plist"]) {
        BQLog(@"ULTRA26 path-check %@ skipped: missing or non-plist target", origin);
        return NO;
    }
    struct stat expected = {0};
    if (stat(canonical.fileSystemRepresentation, &expected) != 0 ||
        !S_ISREG(expected.st_mode) || expected.st_size <= 0 ||
        expected.st_size > 1024 * 1024) {
        BQLog(@"ULTRA26 path-check %@ skipped: canonical file metadata unavailable", origin);
        return NO;
    }
    errno = 0;
    int fd = open(candidate.fileSystemRepresentation, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    int openError = fd < 0 ? errno : 0;
    if (fd < 0) {
        BQLog(@"ULTRA26 path-check %@ denied errno=%d (%s)", origin,
              openError, strerror(openError));
        return NO;
    }
    struct stat actual = {0};
    BOOL same = fstat(fd, &actual) == 0 && S_ISREG(actual.st_mode) &&
                actual.st_dev == expected.st_dev &&
                actual.st_ino == expected.st_ino &&
                actual.st_size == expected.st_size;
    close(fd);
    BQLog(@"ULTRA26 path-check %@ O_RDWR=YES same-canonical-file=%d", origin, same);
    if (!same) return NO;
    gUltra26ActiveMGPath = [candidate copy];
    return YES;
}

static int64_t bq_ultra26_exact_file_escape(NSString **linkOut) {""",
"compare MCM returned path with canonical MobileGestalt inode")

replace_once(m,
"""    gUltra26LastTrials = 0;
    gUltra26LastTokens = 0;
    gUltra26LastWritable = 0;

    NSString *full =""",
"""    gUltra26LastTrials = 0;
    gUltra26LastTokens = 0;
    gUltra26LastWritable = 0;
    gUltra26ActiveMGPath = nil;

    NSString *full =""",
"reset active file path before every bounded query")

replace_once(m,
"""            errno = 0;
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
            }""",
"""            BOOL canWrite = bq_ultra26_verified_writable_path(
                file, full, @"canonical-file");
            if (!canWrite && resultPath.length) {
                // V2 never tested the returned MCM alias; watchOS path matching
                // may differ. This does not imply the alias is writable.
                canWrite = bq_ultra26_verified_writable_path(
                    resultPath, full, @"MCM-result-path");
            }
            BQLog(@"ULTRA26 exact-file token=%lld flags=0x%llx writable=%d mode=%@",
                  (long long)handle, (unsigned long long)flags[i], canWrite,
                  gUltra26ActiveMGPath.length &&
                  ![gUltra26ActiveMGPath isEqualToString:file]
                      ? @"MCM alias" : @"canonical/denied");
            if (canWrite) {
                ++gUltra26LastWritable;
                gUltra26WritableCandidateSeen = YES;
                if (linkOut) *linkOut = kMGCaches;
                BQLog(@"ULTRA26 WRITABLE CANDIDATE: verified file identity and O_RDWR on %@; no bytes changed",
                      gUltra26ActiveMGPath);
                return handle;
            }""",
"probe MCM-returned alias and reject tokens without writable matching inode")

replace_once(m,
"""    if (handle >= 0) {
        if (g.release) g.release(handle);
        gUltra26NormalCandidateFailed = NO;
    }
    NSString *answer =""",
"""    if (handle >= 0) {
        if (g.release) g.release(handle);
        gUltra26NormalCandidateFailed = NO;
    }
    // Preflight never retains a live file authorization for later operations.
    gUltra26ActiveMGPath = nil;
    NSString *answer =""",
"discard manual-probe path after releasing its token")

# BQSaveGestalt creates an escaped path from the returned container directory;
# for a verified MCM alias, keep the ORIGINAL raw path, not a standardized
# path that silently removes the traversal components.
replace_once(m,
"""    NSString *plist = [link stringByAppendingPathComponent:@"com.apple.MobileGestalt.plist"];
    NSData *beforeWrite = [NSData dataWithContentsOfFile:plist];""",
"""    NSString *plist = gUltra26ActiveMGPath.length
        ? gUltra26ActiveMGPath
        : [link stringByAppendingPathComponent:@"com.apple.MobileGestalt.plist"];
    NSData *beforeWrite = [NSData dataWithContentsOfFile:plist];""",
"route saved content through the exact validated MCM file path")

replace_once(m,
"""    if (bq_ultra26_build() && [path isEqualToString:BQMobileGestaltPath()]) {""",
"""    if (bq_ultra26_build() &&
        ([path isEqualToString:BQMobileGestaltPath()] ||
         (gUltra26ActiveMGPath.length && [path isEqualToString:gUltra26ActiveMGPath]))) {""",
"apply protected-file write safety to both canonical and MCM alias paths")

replace_once(m,
"""        struct stat st;
        BOOL ready = fstat(fd, &st) == 0 && S_ISREG(st.st_mode) &&
                     st.st_size == (off_t)original.length &&
                     [bq_read_exact(fd, original.length) isEqualToData:original];""",
"""        struct stat st = {0}, canonicalSt = {0};
        BOOL ready = fstat(fd, &st) == 0 && S_ISREG(st.st_mode) &&
                     stat(BQMobileGestaltPath().fileSystemRepresentation, &canonicalSt) == 0 &&
                     st.st_dev == canonicalSt.st_dev &&
                     st.st_ino == canonicalSt.st_ino &&
                     st.st_size == canonicalSt.st_size &&
                     st.st_size == (off_t)original.length &&
                     [bq_read_exact(fd, original.length) isEqualToData:original];""",
"recheck alias inode against protected original immediately before writing")

replace_once(m,
"""void BQEndEscape(int64_t handle) {
    if (handle >= 0 && g.release) g.release(handle);""",
"""void BQEndEscape(int64_t handle) {
    if (handle >= 0 && g.release) g.release(handle);
    gUltra26ActiveMGPath = nil;""",
"discard untrusted alias when sandbox extension is released")

# UI makes clear that this is a NEW path hypothesis, not a native-face fix.
replace_once("HermesEnabler2/BackupView.swift",
"""Button("Probe WatchOS 26 Writable Token") {""",
"""Button("Try MCM-Returned File Path") {""",
"label experimental alias preflight unambiguously")

print("V3 READY: alternate MCM-returned file path, inode identity, write safeguards.")
