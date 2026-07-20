# Axis Studio and MocapApi references

This index records the sources used to design interoperable behavior and an original UI.
No screenshots, logos, meshes, pose animations, or other Noitom assets are redistributed.

The control-by-control evidence, placement gaps, and support classifications are
maintained in [PARITY_LEDGER.md](PARITY_LEDGER.md). The open-source screenshot
protocol is documented in [VISUAL_TESTING.md](VISUAL_TESTING.md). Linux USB,
Wine, and Windows-VM boundaries are recorded in
[USB_ON_LINUX.md](USB_ON_LINUX.md).

## Primary product references

- Attached **Axis Studio Manual**, 52 PDF pages, supplied by the project owner. Useful
  sections: device/settings (PDF 5–7), calibration (10–15), viewport and suits (23–27),
  record/project/edit/export (29–35), broadcast (36–38), data formats (39–52).
- [Current Noitom English support manual entry](https://support.noitom.com.cn/s/customer-manual-en/doc/1-software-installation-and-activation-yC49l3MCA2)
- [Official Axis Studio manual root](https://noitom.gitbook.io/axis-studio-manual)
- [Start capture and calibration](https://noitom.gitbook.io/axis-studio-manual/kai-shi-dong-bu)
- [Real-time capture interface](https://noitom.gitbook.io/axis-studio-manual/shi-shi-dong-bu-xiang-jie)
- [Data editing](https://noitom.gitbook.io/axis-studio-manual/shu-ju-bian-ji)
- [Export and broadcasting](https://noitom.gitbook.io/axis-studio-manual/shu-ju-dao-chu-yu-guang-bo)
- [Project management](https://noitom.gitbook.io/axis-studio-manual/gong-cheng-guan-li)
- [Axis Studio 2.8 changes](https://noitom.gitbook.io/axis-studio-manual/2.8-ban-ben-zhu-yao-geng-xin)
- [Official Axis Studio product page](https://en.noitom.com.cn/axis-studio.html)
- [Official Noitom software downloads](https://noitom.com/serviceinfo.html?id=1)

## Wire protocol and API references

- [Noitom Neuron Data Reader Runtime API](https://shopcdn.noitom.com.cn/newimage/0c51544770fb49d79f814e9446875121.pdf):
  published BVH value layout, default YXZ channels, 59-joint order, 64-byte header,
  UDP listener, TCP client, and packet-loss caveat.
- [MocapApi repository](https://github.com/pnmocap/MocapApi)
- [MocapApi English API reference](https://github.com/pnmocap/MocapApi/blob/main/doc/MocapApi_en.md)
- [MocapApi C++ interface](https://github.com/pnmocap/MocapApi/blob/main/include/MocapApi.h)
- [MocapApi C proc-table interface](https://github.com/pnmocap/MocapApi/blob/main/include/MocapCApi.h)
- [Legacy Axis Neuron user guide](https://usermanual.wiki/Document/AxisUserGuideFinal0923.414981546.pdf):
  documented `index name values ||` string frames and the older binary header.

## Design findings carried into the UI

- Dense, rectilinear dark panels with blue selection and cyan active-viewport outlines.
- A central orbit/pan/zoom viewport, a suits/sensor inspector, capture controls, and a
  lower take/timeline/event region.
- Signal uses a round status marker; magnetic environment uses a square marker. Labels
  and numbers accompany colors for accessibility.
- Calibration is a server-driven state machine: pose names, countdown, and progress come
  from the connected capability rather than one hard-coded Axis version.
- Axis-only solver, body-dimension, project, `.mbx`, contact-processing, and exporter
  controls are absent or explicitly read-only.

## Licensing reference

- [GitHub: Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

At the audited upstream commit `a62cb6dfc33fc74631c5732bd90f7a91bebe622a`, the
repository has no `LICENSE`, `COPYING`, or `NOTICE`. GitHub's fork mechanism does not
turn default-copyright material into open-source software. The app's Apache-2.0 license
therefore covers only newly authored files under `studio/`, the root `install.sh`, and
the `.github/workflows/mocap-studio-*.yml` workflows identified by their SPDX headers.
