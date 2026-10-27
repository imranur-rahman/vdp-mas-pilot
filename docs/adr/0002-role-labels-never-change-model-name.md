# Role labels never change the model name a system sees

The handoff proposed labelling each agent's role by giving it a model alias such as `mantis.critic`. We rejected that because systems make decisions based on the model name. Mantis, for example, checks the name to decide whether the model supports adaptive thinking, and routes by its prefix. An alias would change the requests a system sends, which breaks our rule of running upstream systems unmodified. Instead, the proxy identifies the role from a per-role API key or URL where a system allows one, and labels every request with a hash of its system prompt as a fallback and cross-check.

**Consequences:** each system's adapter must say how its roles are told apart. For systems with no per-role setting (the Anthropic harness, AutoCVE), the prompt hash may be the only label.
