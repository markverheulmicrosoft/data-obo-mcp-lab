import { broadcastResponseToMainFrame } from "@azure/msal-browser/redirect-bridge";
import { safeErrorCode } from "./presentation.mjs";

const status = document.getElementById("auth-status");
if (window.location.hash || /[?&](code|error|state)=/.test(window.location.search)) {
  broadcastResponseToMainFrame().catch((error) => {
    status.textContent = `De aanmelding kon niet worden teruggegeven (${safeErrorCode(error, "AUTH_CALLBACK_FAILED")}). Sluit dit venster en meld opnieuw aan in het lab.`;
  });
} else {
  status.textContent = "Je kunt dit venster sluiten en teruggaan naar het lab.";
}
