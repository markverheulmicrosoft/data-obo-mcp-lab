param name string
param location string
param tags object
param tenantId string
param writeSecret bool

@secure()
param gatewayClientSecret string

resource vault 'Microsoft.KeyVault/vaults@2026-05-15' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}

resource gatewaySecret 'Microsoft.KeyVault/vaults/secrets@2026-05-15' = if (writeSecret) {
  parent: vault
  name: 'gateway-client-secret'
  tags: tags
  properties: {
    value: gatewayClientSecret
  }
}

output name string = vault.name
output secretUri string = '${vault.properties.vaultUri}secrets/gateway-client-secret'
