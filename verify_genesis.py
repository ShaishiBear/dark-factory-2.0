import hashlib, json, os, subprocess, sys

def out(*a): return subprocess.run(["git", *a], capture_output=True).stdout
def blob(p): return out("cat-file", "blob", "HEAD:" + p)

ok = True
def check(label, actual, expected):
    global ok
    good = actual == expected
    ok = ok and good
    print(("OK       " if good else "MISMATCH ") + label)
    if not good:
        print("   expected " + expected)
        print("   actual   " + actual)

check("candidate commit", out("rev-parse", "HEAD").decode().strip(),
      "6e3d8d86bc583815f240c36e4bf579acdb28f6ba")
check("candidate tree", out("rev-parse", "HEAD^{tree}").decode().strip(),
      "5cef15395594f7e8e450fdbccaa3e78e92af486a")

for path, expected in {
    "harness/genesis_validate.py":     "03cb4bfeb63613a601ec5d18cf02976d46f66cd1756a01c9cf40f9e3ad5d0f5a",
    "harness/genesis-recipe.json":     "fb13656eae5bf9a4965d7a85e615ccd36681edb4ecb74bf56c30a14f18573db5",
    "harness/genesis_collect.py":      "9969f61b46d16a5340774b7499d31c61a200b088add2926513845bd999c71236",
    ".factory/bootstrap/genesis.json": "f13397a817b282fd4bd315842575c305006220fa1b79fba6eefaea232cc10ef9",
    "harness/bootstrap_verify.py":     "a54cff4b85ac956105e5ef356ed5eccef3b9e8e4d332d5c593a564ffb4e5d822",
}.items():
    check(path, hashlib.sha256(blob(path)).hexdigest(), expected)

policy = os.path.expanduser("~/genesis-policy.json")
if os.path.isfile(policy):
    check("external policy", hashlib.sha256(open(policy, "rb").read()).hexdigest(),
          "fa06867b944636e3188da87f4946ed582dd5c910a9bcc3fca766d6e30bb7c5df")
else:
    print("SKIPPED  no policy at " + policy)

inv = json.loads(subprocess.run([sys.executable, "harness/bootstrap_verify.py",
    "--commit", "HEAD", "--emit-inventory",
    "factory_kernel/", "harness/", "tests/factory/", "scripts/", ".factory/prompts/",
    ".factory/holdout/", ".factory/benchmark/", ".factory/locks/", ".github/",
    "deploy/systemd/", ".factory/architecture.json", ".factory/evidence-spine.json",
    ".factory/kernel.json", ".factory/decisions.md", "MISSION.md", "FACTORY_RULES.md",
    "CLAUDE.md"], capture_output=True).stdout)
man = json.loads(blob(".factory/bootstrap/genesis.json"))
same = inv["trust_root"] == man["trust_root"]
ok = ok and same
print(("OK       " if same else "MISMATCH ") +
      "inventory matches manifest (" + str(len(man["trust_root"])) + " files)")

print()
print("ALL CHECKS PASSED" if ok else "SOMETHING DOES NOT MATCH - DO NOT PROCEED")
