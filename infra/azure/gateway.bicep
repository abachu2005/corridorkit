targetScope = 'resourceGroup'

@description('Azure region for the scale-to-zero job gateway.')
param location string = resourceGroup().location

@description('Globally unique, lowercase Container Apps environment prefix.')
@minLength(3)
@maxLength(20)
param namePrefix string

@description('Immutable gateway image reference; prefer an image digest.')
param containerImage string

@secure()
@description('Bearer token expected by the gateway. Prefer Key Vault in production.')
param endpointToken string

@description('Container registry hostname.')
param registryServer string

@description('Container registry pull username.')
param registryUsername string

@secure()
@description('Container registry pull password.')
param registryPassword string

@description('Hard concurrency ceiling for this guarded reference deployment.')
@minValue(1)
@maxValue(1)
param maxReplicas int = 1

@description('Globally unique storage account name for durable job state.')
@minLength(3)
@maxLength(24)
param storageAccountName string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
  }
}

resource shareService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource jobsShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: shareService
  name: 'jobs'
  properties: {
    enabledProtocols: 'SMB'
    shareQuota: 20
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'cpu-worker'
        workloadProfileType: 'D4'
        minimumCount: 0
        maximumCount: 1
      }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'jobs'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: jobsShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource gateway 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-gateway'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'cpu-worker'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'endpoint-token'
          value: endpointToken
        }
        {
          name: 'registry-password'
          value: registryPassword
        }
      ]
      registries: [
        {
          server: registryServer
          username: registryUsername
          passwordSecretRef: 'registry-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'gateway'
          image: containerImage
          env: [
            {
              name: 'ENDPOINT_TOKEN'
              secretRef: 'endpoint-token'
            }
            {
              name: 'SKULLBASE_JOB_ROOT'
              value: '/data'
            }
          ]
          resources: {
            cpu: json('4.0')
            memory: '16Gi'
          }
          volumeMounts: [
            {
              volumeName: 'jobs'
              mountPath: '/data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'jobs'
          storageType: 'AzureFile'
          storageName: environmentStorage.name
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '1'
              }
            }
          }
        ]
      }
    }
  }
}

output endpointUrl string = 'https://${gateway.properties.configuration.ingress.fqdn}'
