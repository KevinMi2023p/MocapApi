AXIS STUDIO SETUP CD
====================

After Windows setup and the virtio network-driver installation:

1. Double-click START-AXIS-SETUP.cmd on this CD.
2. If the VM was created with --axis-installer, the helper starts that
   installer interactively. `.zip` packages are extracted first; the helper
   only auto-runs one unambiguous installer. Otherwise it opens Noitom's
   official installation guide and account portal so you can download the
   edition assigned to your license.
3. For an online license, create or sign in to your Noitom account, click
   Register, enter the kit's Product ID, and verify it.
4. Launch the ONLINE edition of Axis Studio and sign in with that same email
   and password.

The setup helper never asks for, copies, or stores account credentials or the
Product ID. Account registration and application login remain interactive
because activation is tied to the licensed account and VM.

Legacy dongle users must use the Axis Studio edition matching their license
and pass the Wibu CodeMeter dongle through to this Windows VM.

Official resources:
  Installation guide:
  https://support.neuronmocap.com/hc/en-us/articles/5497866781467-Installation

  Noitom account portal:
  https://account.noitom.com/

After activation, follow vm/README.md in the MocapApi checkout on the Linux
host to attach the sensor transceiver, configure its RNDIS adapter, and enable
BVH broadcasting.
