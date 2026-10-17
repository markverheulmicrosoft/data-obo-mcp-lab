targetScope = 'resourceGroup'

param botName string
param oauthClientId string
param subjectId string
param tenantId string
param gatewayClientId string
param foundryAccountName string
param foundryProjectName string
param testUserIds array

resource bot 'Microsoft.BotService/botServices@2022-09-15' existing = {
  name: botName
}

resource oauth 'Microsoft.BotService/botServices/connections@2022-09-15' = {
  parent: bot
  name: 'mcp-user'
  location: 'global'
  kind: 'azurebot'
  properties: {
    clientId: oauthClientId
    scopes: 'api://${gatewayClientId}/access_as_user'
    serviceProviderId: 'c00b44ab-5e16-c44c-af26-2fd5bc55eb18'
    parameters: [
      { key: 'UniqueIdentifier', value: subjectId }
      { key: 'TenantId', value: tenantId }
      { key: 'LoginUri', value: 'https://login.microsoftonline.com' }
      { key: 'IsMultiTenantFlowSupported', value: 'false' }
    ]
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: foundryProjectName
}

var consumerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'eed3b665-ab3a-47b6-8f48-c9382fb1dad6')

resource consumers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for userId in testUserIds: {
  name: guid(project.id, userId, consumerRole)
  scope: project
  properties: {
    roleDefinitionId: consumerRole
    principalId: userId
    principalType: 'User'
  }
}]

output connectionName string = oauth.name
