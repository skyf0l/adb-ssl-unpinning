#!/usr/bin/env python3

from adbutils import AdbClient, AdbDevice
from sys import argv
import os
from pathlib import Path
import shutil
import subprocess as sp
import xml.etree.ElementTree as ET


# Default ADB
ADB_HOST = '127.0.0.1'
ADB_PORT = 5037

APKTOOL = 'apktool_2.6.1.jar'
JAR_SIGNER = 'uber-apk-signer-1.2.1.jar'


def pull_package(device: AdbDevice, package_name: str, output_path: Path):
    for apk in device.shell('pm path ' + package_name).splitlines():
        apk_path = apk.strip().split(':')[1]
        print(f'Pulling {apk_path}...')
        device.sync.pull(apk_path, output_path / apk_path.split('/')[-1])


def patch_manifest(unpacked_apk_path: Path):
    manifest_path = unpacked_apk_path / "AndroidManifest.xml"
    root = ET.parse(manifest_path).getroot()
    application = root.find("application")

    if application.get("{http://schemas.android.com/apk/res/android}networkSecurityConfig") is None:
        # Add networkSecurityConfig attribute
        application.set("{http://schemas.android.com/apk/res/android}networkSecurityConfig", "@xml/network_security_config")
        with open(manifest_path, "w") as f:
            f.write(ET.tostring(root).decode())


def add_network_security_config(unpacked_apk_path: Path):
    with open(unpacked_apk_path / "res" / "xml" / "network_security_config.xml", "w") as f:
        f.write("""<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <debug-overrides>
        <trust-anchors>
            <certificates src="user" />
        </trust-anchors>
    </debug-overrides>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>
""")


def patch_package(device: AdbDevice, package_name: str):
    # Pull original APK
    original_output = Path(package_name)
    if not os.path.exists(original_output):
        os.mkdir(original_output)
        pull_package(device, package_name, original_output)

    # Remove old patched output directory and create new one
    patched_output = Path(package_name + '_patched')
    if os.path.exists(patched_output):
        shutil.rmtree(patched_output)
    os.mkdir(patched_output)

    # Process each APK
    for apk in os.listdir(original_output):
        file_name = os.path.splitext(apk)[0]

        apk_path = original_output / apk
        unpacked_apk_path = patched_output / file_name
        packed_apk_path = patched_output / (file_name + '.repack.apk')
        signed_apk_path = patched_output / (file_name + '.repack-aligned-debugSigned.apk')
        patched_apk_path = patched_output / (file_name + '_patched.apk')

        # Unpack base APK
        if file_name == 'base':
            sp.run(["java", "-jar", APKTOOL, "d", apk_path, "-o", unpacked_apk_path, "-s"])
        else:
            sp.run(["java", "-jar", APKTOOL, "d", apk_path, "-o", unpacked_apk_path, "-s", "-r"])

        # Disable SSL pinning
        if file_name == 'base':
            patch_manifest(unpacked_apk_path)
            add_network_security_config(unpacked_apk_path)

        # Repack APK
        if sp.run(["java", "-jar", APKTOOL, "b", unpacked_apk_path, "-o", packed_apk_path]).returncode != 0:
            sp.run(["java", "-jar", APKTOOL, "b", unpacked_apk_path, "-o", packed_apk_path, "--use-aapt2"])

        # Sign APK
        sp.run(["java", "-jar", JAR_SIGNER, "-a", packed_apk_path])

        # Clean up
        os.remove(packed_apk_path)
        shutil.rmtree(unpacked_apk_path)

        os.rename(signed_apk_path, patched_apk_path)

    # Push patched APKs back to device
    print('Uninstalling package...')
    device.uninstall(package_name)

    # Clean up
    print('Installing patched APKs...')
    os.system('adb install-multiple ' + ' '.join([str(patched_output / apk) for apk in os.listdir(str(patched_output))]))


if __name__ == '__main__':
    if len(argv) < 3:
        print('Usage: python3 patch_apk.py <serial> <package_name>')
        exit(1)

    # Connect to the ADB server
    client = AdbClient(host=ADB_HOST, port=ADB_PORT)
    device = client.device(argv[1])

    patch_package(device, argv[2])
