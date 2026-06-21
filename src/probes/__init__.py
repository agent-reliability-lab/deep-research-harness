"""Provider qualification probes (spec: "Provider qualification gate").

A provider must pass G1 (identity), G2 (cache accounting), and G3 (tool
fidelity) before C0 development, and G4 (20-request stability soak) before any
eval-valid run. Probe artifacts are written under
``results/provider-probes/<provider>/`` and committed as evidence.
"""
