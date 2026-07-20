# Isolated Wine reference lab

This developer-only tool attempts to launch a user-supplied Axis Studio Windows
directory under Wine and captures a fixed 1920×1080 screenshot plus startup
diagnostics. It exists solely to document interoperable UI behavior. It is not
part of Mocap Studio release archives.

The container intentionally runs:

- without network access or host USB devices;
- as an unprivileged user with all Linux capabilities dropped;
- with a read-only root filesystem and read-only Axis Studio mount;
- with bounded, disposable in-memory Wine, temporary, and output directories;
- with CPU, memory and process limits.

After Axis Studio has ended or the bounded observation interval has elapsed,
the harness terminates Wine and exports a fixed artifact list from the in-memory
directory. The proprietary process never receives a writable host mount. The
container is then removed. Do not place proprietary screenshots, logs,
executables, DLLs, firmware, icons or other vendor assets in Git.

## Run

Docker is the only host requirement. Extract the vendor ZIP outside this
repository, then run:

```sh
studio/tools/wine-reference/capture.sh \
  "/absolute/path/to/Axis Studio" \
  "/absolute/path/to/private-reference-output"
```

The main executable in the supplied reference package has no detected
Authenticode signature. Confirm the package provenance and checksum before
running it. The supplied archive audited for this project had SHA-256:

```text
c97a33cf05715dd2b70cf33daf5ef987a3f3cfb3e9a5223efe6d935e20c018d4
```

The application may stop at a licensing or runtime prerequisite dialog. That is
a useful diagnostic result; this harness does not install CodeMeter, Microsoft
runtime packages or vendor drivers and does not attempt activation.

For a narrowly scoped compatibility experiment, `AXIS_WINE_DEBUG` changes
Wine's diagnostic channels and `AXIS_WINE_DLL_OVERRIDES` changes DLL override
rules inside the disposable prefix.

The default override is `hnetcfg=d`. Axis v3 enumerates Windows Firewall rules
at startup. Wine 9's `INetFwRules::_NewEnum` implementation returns
`E_NOTIMPL` with a null enumerator, but Axis dereferences that null pointer at
`0x1400033dd`. Wine 11.0 source retains the same unimplemented method. Disabling
Wine's incomplete `hnetcfg` builtin makes COM activation fail earlier, which
Axis handles, and the login window opens. This was confirmed only in the
network-disabled container; it is not a recommendation to disable firewall
integration in a general Wine prefix. To reproduce the original failure, set
`AXIS_WINE_DLL_OVERRIDES` to an explicitly empty value.

## Explicit limitations

This lab cannot validate sensor communication, CodeMeter licensing, Windows
drivers, firmware operations or timing. Wine does not provide a general-purpose
way to run arbitrary vendor Windows kernel drivers. Use a disposable Windows VM
with narrowly scoped USB passthrough for authoritative hardware validation.
