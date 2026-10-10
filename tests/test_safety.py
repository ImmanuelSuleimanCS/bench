import subprocess

from bench.safety import launch_folder_problems


def make_repo(folder):
    subprocess.run(["git", "init", "-q", str(folder)], check=True)


def test_plain_folder_inside_root_passes(tmp_path):
    home = tmp_path / "home"
    work = home / "Projects" / "x"
    work.mkdir(parents=True)
    assert launch_folder_problems(work, [home / "Projects"], home=home) == []


def test_not_a_repository_is_fine(tmp_path):
    home = tmp_path / "home"
    work = home / "Documents" / "Client"
    work.mkdir(parents=True)
    assert launch_folder_problems(work, [work], home=home) == []


def test_home_repository_is_refused(tmp_path):
    home = tmp_path / "home"
    work = home / "Projects" / "x"
    work.mkdir(parents=True)
    make_repo(home)
    problems = launch_folder_problems(work, [home / "Projects"], home=home)
    assert any("rooted at your home folder" in p for p in problems)


def test_own_repository_inside_home_is_fine(tmp_path):
    home = tmp_path / "home"
    work = home / "Projects" / "x"
    work.mkdir(parents=True)
    make_repo(work)
    assert launch_folder_problems(work, [home / "Projects"], home=home) == []


def test_outside_roots_is_refused(tmp_path):
    home = tmp_path / "home"
    other = tmp_path / "elsewhere"
    other.mkdir(parents=True)
    problems = launch_folder_problems(other, [home / "Projects"], home=home)
    assert any("outside the allowed roots" in p for p in problems)


def test_symlink_pointing_outside_is_refused(tmp_path):
    home = tmp_path / "home"
    root = home / "Projects"
    root.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = root / "sneaky"
    link.symlink_to(outside)
    problems = launch_folder_problems(link, [root], home=home)
    assert any("outside the allowed roots" in p for p in problems)


def test_home_folder_itself_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    problems = launch_folder_problems(home, [home], home=home)
    assert any("your home folder" in p for p in problems)


def test_missing_folder_is_refused(tmp_path):
    assert launch_folder_problems(tmp_path / "nope", [tmp_path]) == [f"{tmp_path / 'nope'} does not exist"]
