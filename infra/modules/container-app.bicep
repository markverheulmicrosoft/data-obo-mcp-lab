param name string
@allowed(['gateway', 'rights'])
param role string
param location string
param tags object
param environmentId string
param identityId string
param registryServer string
param secretUri string
param tenantId string
param gatewayClientId string
param rightsClientId string
param gatewayUrl string
param rightsUrl string
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var isPlaceholder = containerImage == 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var isGateway = role == 'gateway'
var port = isPlaceholder ? 80 : 8000
var commonEnv = [
  { name: 'HOST', value: '0.0.0.0' }
  { name: 'PORT', value: '8000' }
  { name: 'ENTRA_TENANT_ID', value: tenantId }
  { name: 'RIGHTS_CLIENT_ID', value: rightsClientId }
  { name: 'RIGHTS_URL', value: rightsUrl }
]
var gatewayEnv = [
  { name: 'GATEWAY_CLIENT_ID', value: gatewayClientId }
  { name: 'GATEWAY_URL', value: gatewayUrl }
]
var secretEnv = [
  { name: 'GATEWAY_CLIENT_SECRET', secretRef: 'gateway-client-secret' }
]

resource app 'Microsoft.App/containerApps@2026-07-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: !isPlaceholder && isGateway
        targetPort: port
        transport: 'auto'
        allowInsecure: false
      }
      registries: isPlaceholder ? [] : [{ server: registryServer, identity: identityId }]
      secrets: isPlaceholder || !isGateway
        ? []
        : [
            { name: 'gateway-client-secret', keyVaultUrl: secretUri, identity: identityId }
          ]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: containerImage
          command: isPlaceholder ? [] : ['python', '-m', isGateway ? 'mcp_lab.gateway' : 'mcp_lab.rights_mcp']
          env: isPlaceholder ? [] : concat(commonEnv, isGateway ? gatewayEnv : [], isGateway ? secretEnv : [])
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            { type: 'Startup', tcpSocket: { port: port }, periodSeconds: 5, failureThreshold: 60 }
            { type: 'Readiness', tcpSocket: { port: port }, periodSeconds: 10, failureThreshold: 3 }
            { type: 'Liveness', tcpSocket: { port: port }, periodSeconds: 30, failureThreshold: 3 }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
