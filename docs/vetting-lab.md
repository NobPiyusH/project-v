# Tool vetting lab (24 Nov 2026)

Purpose: before a third-party tool (ComfyUI custom node, model, program, script) joins the pipeline, read it and write a one-page report.
`python tools/vetting/vet_tool.py PATH --out report.md` reads files only; it never runs, imports or installs the target.

Verdict: HIGH = do not adopt without a human review of each finding; MEDIUM = needs the checklist; LOW = nothing alarming found by reading (not proof of safety).
Flags: download-and-run, obfuscated blobs, keys plus network in one file, install-time hooks, prebuilt binaries, pickle weights, unfamiliar domains, missing or non-commercial licence.

## Checklist before adopting
1. Source is the official repo or release; note the exact commit or release hash and pin it.
2. Licence allows a monetised YouTube channel (see reports/AI video studio landscape.md for the models already checked).
3. Run `vet_tool.py` on the downloaded folder (own empty folder, never run from inside it).
4. Prebuilt binaries: compare the sha256 with the one the vendor publishes.
5. First run on a throwaway pod with no HF_TOKEN, no RUNPOD key and no saved credentials in its environment.
6. Allow or block each network domain on purpose; weights as safetensors, or torch.load with weights_only=True.
7. Write the verdict and the pinned hash in the pipeline docs.

Example: the SeedVR2 ComfyUI node scored HIGH on first scan only because of an install line in its README (curl | sh for uv, now reported as documentation); real findings were an unsafe torch.load and a dynamic exec, both worth a look before use.
