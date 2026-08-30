"""The image installs what the project declares.

The Dockerfile carried its own hand-written pip list beside pyproject.toml's
dependency block, and the two drifted: google-cloud-aiplatform was declared and
was never installed, so the managed Memory Bank initialised fine on every
developer machine and fell back to local in production. The facade degrades
quietly by design, so nothing failed — it just silently stopped being managed.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _declared() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text()
    block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    return {re.split(r"[><=!\[]", line.strip().strip('",'))[0].strip()
            for line in block.strip().splitlines() if line.strip().startswith('"')}


class TestTheImageInstallsWhatIsDeclared:
    def test_the_dockerfile_installs_from_pyproject(self):
        docker = (ROOT / "Dockerfile").read_text()
        assert "pip install --no-cache-dir ." in docker, (
            "the image installs a hand-written list that can drift from "
            "pyproject.toml — which is how aiplatform went missing in production"
        )

    def test_no_second_dependency_list_in_the_dockerfile(self):
        docker = (ROOT / "Dockerfile").read_text()
        install_lines = [l for l in docker.splitlines()
                         if l.startswith("RUN pip install")]
        for line in install_lines:
            assert "google-adk" not in line, f"a second dependency list: {line}"

    def test_the_managed_memory_bank_dependency_is_declared(self):
        assert "google-cloud-aiplatform" in _declared()

    def test_every_backend_a_pillar_claims_has_its_client_declared(self):
        """A managed pillar whose SDK is not a dependency cannot be managed in
        the deployed image, whatever the docs say."""
        declared = _declared()
        for pillar, package in [
            ("Memory Bank", "google-cloud-aiplatform"),
            ("Content Scanning", "google-cloud-modelarmor"),
            ("Event Bus", "google-cloud-pubsub"),
            ("State", "google-cloud-firestore"),
            ("Agent Runtime", "google-adk"),
        ]:
            assert package in declared, f"{pillar} claims managed; {package} is not declared"


class TestTheProjectIsInstallable:
    """`pip install .` failed outright: the distribution is named crisismesh,
    the code is in src/, and hatchling was given nothing to resolve that. It
    went unnoticed because the Dockerfile installed its own package list and
    developers use `pip install -e .` — so the reproducible-setup path in the
    README was the one path nobody ran."""

    def test_the_wheel_target_is_declared(self):
        text = (ROOT / "pyproject.toml").read_text()
        assert "[tool.hatch.build.targets.wheel]" in text
        assert 'packages = ["src"]' in text

    def test_the_readme_install_command_matches_what_works(self):
        readme = (ROOT / "README.md").read_text()
        assert "pip install -e" in readme
