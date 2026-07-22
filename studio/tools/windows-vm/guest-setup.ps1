# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
#
# guest-setup.ps1 - provision Axis Studio inside the axis-studio Windows VM.
#
# Run this INSIDE the Windows guest from the payload CD that axis-vm.sh
# --build-payload-iso created. It must run ELEVATED (msiexec /qn and
# copying into Program Files fail otherwise): open an elevated PowerShell
# prompt (right-click Start -> Terminal (Admin)), then:
#
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   & E:\guest-setup.ps1               # use the drive labeled AXIS_PAYLOAD
#   & E:\guest-setup.ps1 -SoftwareGl   # also deploy Mesa llvmpipe OpenGL
#
# The payload CD is the drive whose volume label is AXIS_PAYLOAD (check
# This PC); the Windows and unattend ISOs occupy the earlier letters.
#
# Every step is idempotent and printed as it runs. This script NEVER touches
# Axis Studio activation: log in interactively with your Noitom account
# (account.noitom.com) inside Axis Studio.

#Requires -RunAsAdministrator

param(
    # Deploy Mesa llvmpipe software OpenGL next to AxisStudio.exe. Only
    # takes effect when a mesa-dist-win archive is on the payload CD.
    [switch]$SoftwareGl
)

$ErrorActionPreference = 'Stop'

$PayloadRoot = $PSScriptRoot
if (-not $PayloadRoot) {
    $PayloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message"
}

function Find-AxisStudioExe {
    # Axis Studio 3 (3.0.14004 MSI) installs to
    # "C:\Program Files\NOITOM\Axis Studio\axis_studio.exe"; older builds
    # used AxisStudio.exe under a directory containing "Axis". Search
    # vendor directories (*Axis* or *Noitom*) under both Program Files
    # roots, bounded to depth 3; the most recently written match wins.
    $roots = @()
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    if (${env:ProgramFiles(x86)}) { $roots += ${env:ProgramFiles(x86)} }
    $found = @()
    foreach ($root in $roots) {
        $vendorDirs = Get-ChildItem -Path $root -Directory `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '*Axis*' -or $_.Name -like '*Noitom*' }
        foreach ($dir in $vendorDirs) {
            try {
                $found += @(Get-ChildItem -Path $dir.FullName -File -Recurse `
                    -Depth 3 -Include 'axis_studio.exe', 'AxisStudio.exe' `
                    -ErrorAction SilentlyContinue)
            } catch {
                # Unreadable subtree; keep searching the other candidates.
            }
        }
    }
    $best = $found | Sort-Object -Property LastWriteTime -Descending |
        Select-Object -First 1
    if ($best) { return $best.FullName }
    return $null
}

function Install-AxisStudio {
    $existing = Find-AxisStudioExe
    if ($existing) {
        Write-Step "Axis Studio is already installed at $existing; skipping install."
        return
    }
    $msi = Get-ChildItem -Path $PayloadRoot -Filter 'Axis*.msi' `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($msi) {
        $log = Join-Path $env:TEMP 'axis-studio-msi.log'
        Write-Step "Installing $($msi.Name) silently (msiexec log: $log)."
        $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList @(
            '/i', "`"$($msi.FullName)`"", '/qn', '/norestart', '/l*v', "`"$log`""
        ) -Wait -PassThru
        if ($process.ExitCode -eq 3010) {
            Write-Step 'Installer requests a reboot (exit 3010); reboot when convenient.'
        } elseif ($process.ExitCode -ne 0) {
            throw "msiexec exited with code $($process.ExitCode); see $log"
        } else {
            Write-Step 'Axis Studio MSI installed.'
        }
        return
    }
    $zip = Get-ChildItem -Path $PayloadRoot -Filter 'Axis*.zip' `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($zip) {
        $dest = 'C:\AxisStudio2-installer'
        if (Test-Path $dest) {
            Write-Step "$dest already exists; skipping extraction."
        } else {
            Write-Step "Extracting $($zip.Name) to $dest."
            Expand-Archive -Path $zip.FullName -DestinationPath $dest
        }
        Write-Step ("Axis Studio 2 ships as an interactive setup program. Run the " +
            "setup executable under $dest manually; its silent-install flags " +
            "are unverified, so this script does not attempt them.")
        return
    }
    Write-Step 'No Axis Studio installer (Axis*.msi / Axis*.zip) found on the payload CD.'
    Write-Step 'On the host, run: ./axis-vm.sh --download-axis 2|3 and --build-payload-iso.'
}

function Install-SoftwareOpenGl {
    # QXL/virtio video gives Windows guests no accelerated OpenGL, but Axis
    # Studio wants OpenGL 4.4+. Mesa llvmpipe (mesa-dist-win, MIT/LGPL)
    # provides software OpenGL 4.5+ when its opengl32.dll sits next to
    # AxisStudio.exe. This fallback is UNVALIDATED until bench-tested.
    if (-not $SoftwareGl) {
        Write-Step 'Skipping software OpenGL (pass -SoftwareGl to deploy Mesa llvmpipe).'
        return
    }
    $archive = Get-ChildItem -Path $PayloadRoot -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'mesa' -and ('.zip', '.7z') -contains $_.Extension } |
        Select-Object -First 1
    if (-not $archive) {
        Write-Step 'No mesa3d/mesa-dist-win archive on the payload CD; skipping.'
        Write-Step 'Download a release from https://github.com/pal1000/mesa-dist-win/releases'
        Write-Step 'into the host payload directory, then rebuild the payload ISO.'
        return
    }
    $axisExe = Find-AxisStudioExe
    if (-not $axisExe) {
        Write-Step 'Axis Studio is not installed yet; install it first, then rerun with -SoftwareGl.'
        return
    }
    $axisDir = Split-Path -Parent $axisExe
    if (Test-Path (Join-Path $axisDir 'opengl32.dll')) {
        Write-Step "opengl32.dll already sits next to AxisStudio.exe in $axisDir; skipping."
        return
    }
    $work = Join-Path $env:TEMP 'mesa-dist-win'
    if (-not (Test-Path $work)) {
        # Extract into a fresh staging directory and rename it into place
        # only on success: a failed or interrupted extraction must never
        # leave a partial $work that a rerun would mistake for a good one.
        $staging = Join-Path $env:TEMP `
            ('mesa-dist-win.tmp.' + [System.IO.Path]::GetRandomFileName())
        Write-Step "Extracting $($archive.Name) to $work."
        New-Item -ItemType Directory -Path $staging | Out-Null
        $extracted = $false
        try {
            if ($archive.Extension -eq '.zip') {
                Expand-Archive -Path $archive.FullName -DestinationPath $staging
                $extracted = $true
            } else {
                # Prefer 7-Zip for .7z archives. Windows 11's tar.exe
                # (bsdtar) can also read 7z, but Windows 10's build cannot
                # (its libarchive lacks LZMA support).
                $sevenZip = $null
                $cmd = Get-Command '7z' -ErrorAction SilentlyContinue
                if ($cmd) {
                    $sevenZip = $cmd.Source
                } elseif (Test-Path 'C:\Program Files\7-Zip\7z.exe') {
                    $sevenZip = 'C:\Program Files\7-Zip\7z.exe'
                }
                if ($sevenZip) {
                    & $sevenZip x $archive.FullName "-o$staging" -y | Out-Null
                    if ($LASTEXITCODE -eq 0) {
                        $extracted = $true
                    } else {
                        Write-Step "7z.exe exited with code $LASTEXITCODE reading $($archive.Name)."
                    }
                } else {
                    $tar = Get-Command 'tar.exe' -ErrorAction SilentlyContinue
                    if ($tar) {
                        & $tar.Source -xf $archive.FullName -C $staging
                        if ($LASTEXITCODE -eq 0) {
                            $extracted = $true
                        } else {
                            Write-Step "tar.exe exited with code $LASTEXITCODE (Windows 10's tar cannot read .7z)."
                        }
                    } else {
                        Write-Step 'Neither 7z.exe nor tar.exe is available on this guest.'
                    }
                }
            }
        } catch {
            Write-Step "Extraction failed: $($_.Exception.Message)"
        }
        if (-not $extracted) {
            Remove-Item -Path $staging -Recurse -Force `
                -ErrorAction SilentlyContinue
            if ($archive.Extension -eq '.zip') {
                Write-Step "Could not extract $($archive.Name); the .zip file may be corrupt. Re-download it and rebuild the payload ISO."
            } else {
                Write-Step "Could not extract $($archive.Name). Install 7-Zip (https://www.7-zip.org) inside the guest and rerun this script; upstream mesa-dist-win publishes only .7z release assets."
            }
            return
        }
        Move-Item -Path $staging -Destination $work
    } else {
        Write-Step "$work already exists; reusing the previous extraction."
    }
    $gl = Get-ChildItem -Path $work -Recurse -Filter 'opengl32.dll' `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } |
        Select-Object -First 1
    if (-not $gl) {
        Write-Step 'Could not locate x64\opengl32.dll in the extracted archive; copy it manually.'
        return
    }
    Write-Step "Copying Mesa x64 DLLs from $($gl.DirectoryName) next to AxisStudio.exe."
    Get-ChildItem -Path $gl.DirectoryName -Filter '*.dll' | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $axisDir -Force
    }
    Write-Step 'Deployed Mesa llvmpipe as the per-application software-OpenGL fallback for this VM (expect low viewport frame rates; capture itself is CPU-side).'
}

function Set-TransceiverIp {
    # The PN Studio transceiver is a Remote NDIS network device. Per the
    # Axis manual the computer side of that link must be a static address
    # in 192.168.1.0/24 with no gateway. The v1.1 guide used 192.168.1.200,
    # newer manuals use .100; either works, we standardize on .100.
    $adapter = Get-NetAdapter -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceDescription -match 'NDIS' } |
        Select-Object -First 1
    if (-not $adapter) {
        Write-Step 'No RNDIS adapter found. Attach the transceiver to the VM first (host: ./axis-vm.sh --attach-usb VID:PID), then rerun this script.'
        return
    }
    Write-Step "RNDIS adapter: $($adapter.Name) ($($adapter.InterfaceDescription))"
    Write-Step 'IPv4 addresses before:'
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue |
        Format-Table -Property IPAddress, PrefixLength -AutoSize | Out-String |
        Write-Host
    $already = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex `
        -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq '192.168.1.100' }
    if ($already) {
        Write-Step 'Static address 192.168.1.100/24 is already set; nothing to do.'
    } else {
        Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
            -ErrorAction SilentlyContinue |
            Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        New-NetIPAddress -InterfaceIndex $adapter.ifIndex `
            -IPAddress 192.168.1.100 -PrefixLength 24 | Out-Null
        Write-Step 'Set static 192.168.1.100/24 (no gateway, per the Axis manual).'
    }
    Write-Step 'IPv4 addresses after:'
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue |
        Format-Table -Property IPAddress, PrefixLength -AutoSize | Out-String |
        Write-Host
}

function Show-BvhContract {
    Write-Host @'

Configure Axis Studio -> Settings -> BVH Broadcasting exactly as follows:

  Enable:              BVH - Capture   (never "Advanced BVH"; that format
                                        is for Maya/MotionBuilder only)
  Frame Format:        Binary ("use old header format" unchecked)
  Rotation:            YXZ
  Displacement:        checked
  Protocol:            UDP
  Local address:       192.168.77.2  port 7001
  Destination address: 192.168.77.1  port 7012

Never broadcast to 255.255.255.255.

On the Linux host, Mocap Studio source settings:
  mode "bvh", transport "udp", host 192.168.77.1 (NOT the default
  127.0.0.1 - loopback never receives guest packets), port 7012,
  rotationOrder "YXZ", unit "centimeters".
'@
}

Write-Step "Payload root: $PayloadRoot"
Install-AxisStudio
Install-SoftwareOpenGl
Set-TransceiverIp
Show-BvhContract
Write-Step 'Done. Activation is interactive and never automated: log in to your Noitom account (account.noitom.com) inside Axis Studio.'
