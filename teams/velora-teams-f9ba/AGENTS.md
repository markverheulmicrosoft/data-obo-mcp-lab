# Hosted Teams lab

This project was built with the microsoft-foundry skill. Before working on or answering questions about foundry agents, read the microsoft-foundry skill first.

The source is in `src/echo-activity/`; `azure.yaml` owns deployment configuration.
This is a Foundry-hosted Activity agent. Verify channel behavior through Teams;
`azd ai agent invoke` does not support Activity.

Keep the agent stateless per question. The model selects fixed read-only tools;
data is returned from the MCP, not invented or read using an app identity.
Bind the MCP's verified user ID to the authenticated Teams sender before data calls.
Keep OAuth tokens outside model input, traces, durable state and source control.
Use only personal chats in the configured lab tenant.

Keep this azd project isolated from the parent repo's existing Bicep/deploy script.
Private environment, OAuth and generated Teams packages stay outside Git.
