targetScope = 'subscription'

param environmentName string
param resourceGroupName string
param containerEnvironmentName string
param gatewayAppName string
param rightsAppName string
param registryName string
param keyVaultName string
param logWorkspaceName string
param appInsightsName string
param gatewayIdentityName string
param rightsIdentityName string
param location string
param sessionId string
param deployedBy string
param createdAt string
param deployerObjectId string
param tenantId string
param gatewayClientId string
param rightsClientId string
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var tags = {
  'app-onboard-skill': 'true'
  'app-onboard-session-id': sessionId
  'created-at': createdAt
  environment: environmentName
  'deployed-by': deployedBy
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module logs './modules/log-analytics.bicep' = {
  name: 'logs'
  scope: rg
  params: { name: logWorkspaceName, location: location, tags: tags }
}

module insights './modules/app-insights.bicep' = {
  name: 'insights'
  scope: rg
  params: { name: appInsightsName, location: location, tags: tags, workspaceId: logs.outputs.id }
}

module registry './modules/registry.bicep' = {
  name: 'registry'
  scope: rg
  params: { name: registryName, location: location, tags: tags }
}

module vault './modules/key-vault.bicep' = {
  name: 'vault'
  scope: rg
  params: {
    name: keyVaultName
    location: location
    tags: tags
    tenantId: tenantId
  }
}

module gatewayIdentity './modules/managed-identity.bicep' = {
  name: 'gateway-identity'
  scope: rg
  params: { name: gatewayIdentityName, location: location, tags: tags }
}

module rightsIdentity './modules/managed-identity.bicep' = {
  name: 'rights-identity'
  scope: rg
  params: { name: rightsIdentityName, location: location, tags: tags }
}

module roles './modules/role-assignments.bicep' = {
  name: 'roles'
  scope: rg
  params: {
    registryName: registry.outputs.name
    keyVaultName: vault.outputs.name
    gatewayPrincipalId: gatewayIdentity.outputs.principalId
    rightsPrincipalId: rightsIdentity.outputs.principalId
    deployerObjectId: deployerObjectId
  }
}

module appEnvironment './modules/container-environment.bicep' = {
  name: 'app-environment'
  scope: rg
  params: {
    name: containerEnvironmentName
    location: location
    tags: tags
    workspaceName: logs.outputs.name
  }
}

var gatewayUrl = 'https://${gatewayAppName}.${appEnvironment.outputs.defaultDomain}/mcp'
var rightsUrl = 'https://${rightsAppName}.internal.${appEnvironment.outputs.defaultDomain}/mcp'

module rights './modules/container-app.bicep' = {
  name: 'rights-app'
  scope: rg
  params: {
    name: rightsAppName
    role: 'rights'
    location: location
    tags: tags
    environmentId: appEnvironment.outputs.id
    identityId: rightsIdentity.outputs.id
    managedIdentityClientId: rightsIdentity.outputs.clientId
    registryServer: registry.outputs.loginServer
    containerImage: containerImage
    tenantId: tenantId
    gatewayClientId: gatewayClientId
    rightsClientId: rightsClientId
    gatewayUrl: gatewayUrl
    rightsUrl: rightsUrl
  }
  dependsOn: [roles]
}

module gateway './modules/container-app.bicep' = {
  name: 'gateway-app'
  scope: rg
  params: {
    name: gatewayAppName
    role: 'gateway'
    location: location
    tags: tags
    environmentId: appEnvironment.outputs.id
    identityId: gatewayIdentity.outputs.id
    managedIdentityClientId: gatewayIdentity.outputs.clientId
    registryServer: registry.outputs.loginServer
    containerImage: containerImage
    tenantId: tenantId
    gatewayClientId: gatewayClientId
    rightsClientId: rightsClientId
    gatewayUrl: gatewayUrl
    rightsUrl: rightsUrl
  }
  dependsOn: [roles, rights]
}

output resourceGroupName string = rg.name
output registryName string = registry.outputs.name
output registryLoginServer string = registry.outputs.loginServer
output keyVaultName string = vault.outputs.name
output gatewayAppName string = gatewayAppName
output rightsAppName string = rightsAppName
output gatewayUrl string = gatewayUrl
output rightsUrl string = rightsUrl
output gatewayActualFqdn string = gateway.outputs.fqdn
output rightsActualFqdn string = rights.outputs.fqdn
