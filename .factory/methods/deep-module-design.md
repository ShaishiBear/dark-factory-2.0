# Method: deep-module design

Good design in this repository means a lot of behaviour behind a small interface, at a clean seam, testable through that interface. Shallow modules that expose many small functions and force callers to orchestrate them are the opposite, even when each function is tidy.

When you design or judge a change:

- **Name the seam.** A seam is a public interface (an exported function, a hook's return value, a route's response, a rendered element with a stable label) through which behaviour can be observed and tested without reaching into internals. Every acceptance criterion must map to a named seam. If you cannot name one, the design is not finished.
- **Put the behaviour behind the seam, not in front of it.** The caller should not need to know the order of internal steps, the shape of intermediate data or which helper to call first. If callers must orchestrate, the module is too shallow; move the orchestration inside.
- **Keep the interface small.** Fewer exported names, fewer parameters, fewer configuration knobs. A parameter that exists for one hypothetical caller is complexity paid by every real caller.
- **Make it testable through the interface.** The acceptance tests will exercise the seam, not the internals. A design that can only be verified by inspecting private state is a design that will be hard to change safely later.
- **One reason to change per module.** If a module changes for two unrelated reasons, it is two modules wearing one name. If two modules always change together, they are one module wearing two names.
- **Respect the layer direction.** The architecture policy names the layers and which may import which. A new dependency edge in the wrong direction is not a design choice; it is a policy violation the governor refuses.
- **Prefer extending an existing deep module over adding a parallel one.** Two modules that do almost the same thing are a maintenance tax and a future bug. If the existing one is close, widen its seam deliberately rather than cloning it.
- **Plan exact files.** The design names every production file the implementation may change and every new file it may create. Vague directories and "and related files" are not a design. The kernel refuses commits outside that set.

Judge a proposed design by asking: can a reader understand what this module does from its interface alone, and can a test prove it through that interface alone? If both answers are yes, the module is deep enough.
