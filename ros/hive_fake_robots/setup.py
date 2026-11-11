from setuptools import setup

package_name = "hive_fake_robots"
setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/fake_fleet.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    description="HIVE fake fleet action servers",
    license="Apache-2.0",
    entry_points={"console_scripts": ["fake_fleet = hive_fake_robots.fake_fleet:main"]},
)
