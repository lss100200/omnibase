from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path


INSTALLER_ROOT = Path(__file__).resolve().parents[1]
WIX_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs"
BAL_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs/bal"
NS = {"w": WIX_NAMESPACE, "bal": BAL_NAMESPACE}


def _xml(relative: str) -> ET.Element:
    return ET.parse(INSTALLER_ROOT / relative).getroot()


def _text(relative: str) -> str:
    return (INSTALLER_ROOT / relative).read_text(encoding="utf-8")


def test_wix_sdk_and_bal_extension_are_pinned_to_the_same_release() -> None:
    package = _xml("package/OmniBase.Package.wixproj")
    bundle = _xml("bundle/OmniBase.Bundle.wixproj")

    assert package.attrib["Sdk"] == "WixToolset.Sdk/7.0.0"
    assert bundle.attrib["Sdk"] == "WixToolset.Sdk/7.0.0"
    assert package.findtext("./PropertyGroup/AcceptEula") == "wix7"
    assert bundle.findtext("./PropertyGroup/AcceptEula") == "wix7"
    assert (
        package.findtext("./PropertyGroup/DefineConstants")
        == "ProductVersion=$(ProductVersion)"
    )
    assert (
        bundle.findtext("./PropertyGroup/DefineConstants")
        == "ProductVersion=$(ProductVersion)"
    )
    references = bundle.findall("./ItemGroup/PackageReference")
    assert [
        (reference.attrib["Include"], reference.attrib["Version"])
        for reference in references
    ] == [("WixToolset.Bal.wixext", "7.0.0")]


def test_generated_payload_is_added_before_wix_core_compile() -> None:
    package = _xml("package/OmniBase.Package.wixproj")
    assert package.findtext("./PropertyGroup/EnableDefaultCompileItems") == "false"
    assert [
        item.attrib["Include"] for item in package.findall("./ItemGroup/Compile")
    ] == ["Product.wxs"]
    assert (
        package.findtext("./PropertyGroup/GeneratedPayloadWxs")
        == "$(MSBuildProjectDirectory)\\obj\\$(Configuration)\\OmniBase.Payload.g.wxs"
    )
    assert package.findtext("./PropertyGroup/SuppressIces") == "ICE64;ICE91"
    target = package.find("./Target[@Name='ValidateOmniBasePayload']")
    assert target is not None
    assert target.attrib["BeforeTargets"] == "CoreCompile"
    assert (
        target.find("./ItemGroup/Compile[@Include='$(GeneratedPayloadWxs)']")
        is not None
    )


def test_msi_is_fixed_current_user_scope_under_local_programs() -> None:
    root = _xml("package/Product.wxs")
    package = root.find("w:Package", NS)
    assert package is not None
    assert package.attrib["Scope"] == "perUser"
    assert package.attrib["Version"] == "$(var.ProductVersion)"
    assert package.attrib["InstallerVersion"] == "500"
    assert package.attrib["Compressed"] == "yes"
    assert re.fullmatch(
        r"\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}",
        package.attrib["UpgradeCode"],
    )

    install_folder = root.find(
        ".//w:StandardDirectory[@Id='LocalAppDataFolder']"
        "/w:Directory[@Id='LocalProgramsFolder'][@Name='Programs']"
        "/w:Directory[@Id='INSTALLFOLDER'][@Name='OmniBase']",
        NS,
    )
    assert install_folder is not None
    rendered = _text("package/Product.wxs")
    assert "ProgramFilesFolder" not in rendered
    assert "ProgramFiles64Folder" not in rendered
    assert "CommonAppDataFolder" not in rendered
    assert "ALLUSERS" not in rendered
    assert "MSIINSTALLPERUSER" not in rendered
    forced_location = root.find(
        "w:Package/w:SetProperty[@Id='INSTALLFOLDER']",
        NS,
    )
    assert forced_location is not None
    assert forced_location.attrib == {
        "Id": "INSTALLFOLDER",
        "Value": "[LocalAppDataFolder]Programs\\OmniBase",
        "Before": "CostFinalize",
        "Sequence": "both",
    }


def test_msi_major_upgrade_is_transactional_and_blocks_downgrades() -> None:
    root = _xml("package/Product.wxs")
    upgrade = root.find("w:Package/w:MajorUpgrade", NS)
    assert upgrade is not None
    assert upgrade.attrib["AllowDowngrades"] == "no"
    assert upgrade.attrib["Schedule"] == "afterInstallInitialize"
    assert upgrade.attrib["MigrateFeatures"] == "yes"
    assert upgrade.attrib["IgnoreRemoveFailure"] == "no"
    assert upgrade.attrib["DowngradeErrorMessage"]

    feature = root.find("w:Package/w:Feature[@Id='OmniBaseFeature']", NS)
    assert feature is not None
    assert feature.attrib["AllowAbsent"] == "no"
    assert feature.find("w:ComponentGroupRef[@Id='OmniBasePayload']", NS) is not None


def test_normal_uninstall_has_no_authored_user_data_target() -> None:
    product = _text("package/Product.wxs")
    bundle = _text("bundle/Bundle.wxs")
    combined = product + bundle

    assert "[LocalAppDataFolder]OmniBase" not in combined
    assert "LocalAppDataFolder\\OmniBase" not in combined
    assert "RemoveFile" not in combined
    assert "RemoveRegistryKey" not in combined
    remove_folders = _xml("package/Product.wxs").findall(".//w:RemoveFolder", NS)
    assert len(remove_folders) == 1
    assert remove_folders[0].attrib["Directory"] == "OmniBaseProgramMenuFolder"


def test_bundle_is_one_embedded_vital_removable_per_user_msi() -> None:
    root = _xml("bundle/Bundle.wxs")
    bundle = root.find("w:Bundle", NS)
    assert bundle is not None
    assert bundle.attrib["Id"] == "OmniBase.Desktop.Bundle"
    assert bundle.attrib["Version"] == "$(var.ProductVersion)"
    assert bundle.attrib["Compressed"] == "yes"
    assert bundle.attrib["DisableRemove"] == "no"

    application = bundle.find(
        "w:BootstrapperApplication/bal:WixStandardBootstrapperApplication",
        NS,
    )
    assert application is not None
    assert application.attrib["SuppressOptionsUI"] == "yes"
    assert application.attrib["SuppressDowngradeFailure"] == "no"
    assert (
        application.attrib["LaunchTarget"]
        == "[LocalAppDataFolder]Programs\\OmniBase\\OmniBase.exe"
    )

    chain = bundle.find("w:Chain", NS)
    assert chain is not None
    children = list(chain)
    assert [child.tag.rsplit("}", 1)[-1] for child in children] == [
        "MsiPackage",
    ]
    (msi,) = children
    assert msi.attrib["SourceFile"] == "$(var.OmniBase.Package.TargetPath)"
    assert msi.attrib["Compressed"] == "yes"
    assert msi.attrib["Visible"] == "no"
    assert msi.attrib["Vital"] == "yes"
    assert msi.attrib["Permanent"] == "no"
    assert "DisplayInternalUI" not in msi.attrib
    install_property = msi.find("w:MsiProperty[@Name='INSTALLFOLDER']", NS)
    assert install_property is not None
    assert install_property.attrib["Value"] == (
        "[LocalAppDataFolder]Programs\\OmniBase"
    )


def test_rollback_probe_is_a_two_msi_transaction_with_an_intentional_blocker() -> None:
    probe_project = _xml("tests/fixtures/RollbackProbe/OmniBase.RollbackProbe.wixproj")
    blocker_project = _xml(
        "tests/fixtures/RollbackProbe/OmniBase.RollbackBlocker.wixproj"
    )
    assert (
        probe_project.findtext("./PropertyGroup/EnableDefaultCompileItems") == "false"
    )
    assert (
        blocker_project.findtext("./PropertyGroup/EnableDefaultCompileItems") == "false"
    )
    assert probe_project.findtext("./PropertyGroup/AcceptEula") == "wix7"
    assert blocker_project.findtext("./PropertyGroup/AcceptEula") == "wix7"
    assert (
        probe_project.findtext("./PropertyGroup/DefineConstants")
        == "ProductVersion=$(ProductVersion)"
    )
    assert (
        blocker_project.findtext("./PropertyGroup/DefineConstants")
        == "ProductVersion=$(ProductVersion)"
    )
    assert probe_project.findtext("./PropertyGroup/SuppressSpecificWarnings") == "1151"
    assert blocker_project.findtext("./PropertyGroup/SuppressIces") == "ICE71"
    assert [
        item.attrib["Include"] for item in probe_project.findall("./ItemGroup/Compile")
    ] == ["RollbackProbeBundle.wxs"]
    assert [
        item.attrib["Include"]
        for item in blocker_project.findall("./ItemGroup/Compile")
    ] == ["RollbackBlocker.wxs"]

    root = _xml("tests/fixtures/RollbackProbe/RollbackProbeBundle.wxs")
    chain = root.find("w:Bundle/w:Chain", NS)
    assert chain is not None
    children = list(chain)
    assert [child.tag.rsplit("}", 1)[-1] for child in children] == [
        "RollbackBoundary",
        "MsiPackage",
        "MsiPackage",
    ]
    assert children[0].attrib["Transaction"] == "yes"
    assert children[0].attrib["Vital"] == "yes"
    assert children[1].attrib["SourceFile"] == "$(var.OmniBase.Package.TargetPath)"
    assert (
        children[2].attrib["SourceFile"] == "$(var.OmniBase.RollbackBlocker.TargetPath)"
    )
    assert all("DisplayInternalUI" not in package.attrib for package in children[1:])
    assert all(package.attrib["Vital"] == "yes" for package in children[1:])
    assert all(package.attrib["Permanent"] == "no" for package in children[1:])

    blocker = _xml("tests/fixtures/RollbackProbe/RollbackBlocker.wxs")
    package = blocker.find("w:Package", NS)
    launch = blocker.find("w:Package/w:Launch", NS)
    assert package is not None
    assert package.attrib["Scope"] == "perUser"
    assert launch is not None
    assert launch.attrib["Condition"] == "0"


def test_production_authoring_has_no_external_runtime_dependencies_or_actions() -> None:
    roots = [_xml("package/Product.wxs"), _xml("bundle/Bundle.wxs")]
    forbidden_elements = {
        "CustomAction",
        "Environment",
        "ExePackage",
        "MspPackage",
        "MsuPackage",
        "ServiceInstall",
        "ServiceControl",
    }
    for root in roots:
        assert not {
            element.tag.rsplit("}", 1)[-1]
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] in forbidden_elements
        }

    authoring = (
        _text("package/Product.wxs")
        + _text("bundle/Bundle.wxs")
        + _text("package/OmniBase.Package.wixproj")
        + _text("bundle/OmniBase.Bundle.wixproj")
    ).casefold()
    for dependency in ("docker", "wsl", "postgres", "pgvector", "bge-m3"):
        assert dependency not in authoring


def test_rollback_probe_is_test_only_and_lifecycle_harness_covers_all_states() -> None:
    bundle_project = _text("bundle/OmniBase.Bundle.wixproj")
    production_bundle = _text("bundle/Bundle.wxs")
    harness = _text("tests/Test-InstallerLifecycle.ps1")

    assert "RollbackProbe" not in bundle_project
    assert "RollbackProbe" not in production_bundle
    for stage in (
        "InstallVersion1",
        "UpgradeVersion2",
        "RejectDowngrade",
        "RollbackFailedUpgrade",
        "UninstallVersion2",
    ):
        assert stage in harness
    assert "OMNIBASE_INSTALLER_E2E" in harness
    assert "DisposableAccountAcknowledged" in harness
    assert "installer_e2e_requires_non_elevated_shell" in harness
    assert "Test-FullyQualifiedWindowsPath" in harness
    assert "IsPathFullyQualified" not in harness
    assert "-or @(Get-OmniBaseRegistration).Count" in harness
    assert "if (@(Get-OmniBaseRegistration).Count" in harness
    assert (
        "Starting a new MSI transaction, id: OmniBaseRollbackProbeTransaction"
        in harness
    )
    assert "Applying execute package: OmniBaseUpgradeCandidate" in harness
    assert "Applied execute package: OmniBaseUpgradeCandidate, result: 0x0" in harness
    assert "Applying execute package: IntentionalRollbackBlocker" in harness
    assert (
        "Applied execute package: IntentionalRollbackBlocker, result: 0x80070643"
        in harness
    )
    assert (
        "Rolling back MSI transaction, id: OmniBaseRollbackProbeTransaction" in harness
    )
    assert (
        "package: OmniBaseUpgradeCandidate, install registration state: Absent"
        in harness
    )
