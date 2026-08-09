# Kubernetes deployment

These manifests provide the production boundary described in the deployment documentation:

- a two-replica backend deployment with readiness/liveness probes;
- a five-minute graceful termination window for long research tasks;
- a short `preStop` drain delay before the ingress removes a pod;
- WebSocket-compatible ingress timeouts and stable `thread_id` routing.

Create `shopping-agent-config` and `shopping-agent-secrets` out of band. Never commit provider
credentials, model keys, or generated runtime data to this directory.
