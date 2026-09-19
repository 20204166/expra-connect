# Extraction Map

| System Analyzer owner | Expra Connect owner | Decision |
|---|---|---|
| `maintenance.nodes.NodeId` | `expra_connect.identity` | Generalized |
| `maintenance.remote_security` | `expra_connect.tls_material` | Direct headless extraction |
| `maintenance.remote_support.transport` | `expra_connect.socket_transport` | Direct headless extraction |
| `maintenance.remote_support.protocol` | `expra_connect.wire_protocol` | Generalized wire contract |
| `maintenance.remote_support.server` | `expra_connect.server` | Headless bounded listener |
| `maintenance.remote` | `expra_connect.remote_service` | Provider/service extraction |
| `maintenance.components.network_discovery` | `expra_connect.discovery_full` | Adapted backend lifecycle |
| `maintenance.cluster_roles` | `expra_connect.role_engine` | Direct role/fencing extraction |
| UI pairing actions | `expra_connect.pairing` | Headless transition core |
| `RemoteService` scanner handlers | `expra_connect.remote_service` | Injected provider only |
| Tk pages/dialogs | None | Excluded |
| CPU/GPU/storage/battery code | None | Excluded |
| Streaming/video/audio/input | None | Excluded |
