import bench


def test_package_imports():
    assert bench.__name__ == "bench"
    assert bench.__version__
