"""Narrow the azd-generated package to the personal, read-only lab experience."""

import json
import zipfile
from pathlib import Path
from uuid import UUID

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = "https://github.com/markverheulmicrosoft/data-obo-mcp-lab"


def build():
    source = PROJECT / "src" / "echo-activity" / "appPackage.zip"
    destination = ROOT / ".deploy" / "velora-teams.zip"
    with zipfile.ZipFile(source) as package:
        manifest = json.loads(package.read("manifest.json"))
        if len(manifest.get("bots", [])) != 1:
            raise ValueError("Verwacht precies de ene gegenereerde bot.")
        bot = manifest["bots"][0]
        UUID(bot["botId"])
        manifest["name"] = {"short": "Velora data-assistent", "full": "Velora data-assistent"}
        manifest["description"] = {
            "short": "Test je eigen Fabric-rechten via MCP.",
            "full": "Persoonlijk leerlab: vraag je toegestane orders of marges op. "
                    "Entra en Fabric bepalen je rechten. Alleen opgeslagen demodata; geen productiegebruik.",
        }
        manifest["developer"] = {
            "name": "Velora lab", "websiteUrl": REPOSITORY,
            "privacyUrl": REPOSITORY + "/blob/main/teams/velora-teams-f9ba/README.md#privacy",
            "termsOfUseUrl": REPOSITORY + "/blob/main/teams/velora-teams-f9ba/README.md#gebruik",
        }
        manifest.pop("webApplicationInfo", None)
        manifest.pop("copilotAgents", None)
        manifest.pop("supportsChannelFeatures", None)
        manifest["validDomains"] = ["token.botframework.com"]
        bot["scopes"] = ["personal"]
        bot["supportsFiles"] = False
        bot["commandLists"] = [{
            "scopes": ["personal"],
            "commands": [
                {"title": "Orders", "description": "Welke orders mag ik zien?"},
                {"title": "Marges", "description": "Welke marges mag ik zien?"},
            ],
        }]
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", payload)
            for name in manifest["icons"].values():
                if Path(name).name != name or not name.endswith(".png"):
                    raise ValueError("Onverwacht icoonpad in het gegenereerde pakket.")
                output.writestr(name, package.read(name))
    with zipfile.ZipFile(destination) as output:
        actual = json.loads(output.read("manifest.json"))
        assert actual["bots"][0]["scopes"] == ["personal"]
        assert actual["bots"][0]["supportsFiles"] is False
        assert "webApplicationInfo" not in actual
    print(destination)


if __name__ == "__main__":
    build()
