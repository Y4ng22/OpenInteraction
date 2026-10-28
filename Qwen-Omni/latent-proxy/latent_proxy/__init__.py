"""latent-proxy: OpenAI-compatible facade that records background latents.

DeepSeek Harness sends its model requests here instead of to the model
server. The proxy forwards them unchanged except for removing the Gateway's
``<latent_task id="..."/>`` correlation marker, and when a turn ends with a
final answer it writes that answer's latent into the store under the Task id.
"""

__version__ = "0.1.0"
