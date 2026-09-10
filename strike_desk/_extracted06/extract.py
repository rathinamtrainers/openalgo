from pathlib import Path
import re

root = Path(r"d:\ATZ\zerotouchalgo RT\openalgo")
guide = (root / "040_iterations/iteration-06/02_implementation_guide.md").read_text(
    encoding="utf-8"
)
tests = (root / "040_iterations/iteration-06/04_test_automation.md").read_text(encoding="utf-8")
out_dir = Path(__file__).resolve().parent
out_dir.mkdir(exist_ok=True)


def dump(text: str, prefix: str) -> None:
    parts = re.split(r"\n### `([^`]+)`[^\n]*\n", text)
    n = 0
    for i in range(1, len(parts), 2):
        name = parts[i]
        body = parts[i + 1]
        fences = re.findall(r"```(?:python|toml|bash|yaml|json|text)?\n(.*?)```", body, re.S)
        for j, code in enumerate(fences):
            safe = name.replace("/", "_").replace(".", "_")
            path = out_dir / f"{prefix}_{n:03d}_{safe}_{j}.txt"
            path.write_text(f"FILE: {name}\n\n{code}", encoding="utf-8")
            n += 1
            print(f"{path.name}: {name} ({len(code.splitlines())} lines, fence {j})")


dump(guide, "g")
print("--- tests ---")
dump(tests, "t")
