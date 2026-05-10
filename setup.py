from setuptools import setup, find_packages


setup(
    name="company-quickcheck",
    version="0.1.0",
    author="Hermes Agent",
    author_email="hermes-pi@raspberrypi.local",
    description="Batch Austrian company status checker using opendata.host API",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/ether-btc/company-quickcheck",
    packages=find_packages(),
    package_data={"": ["*.md", "*.txt"]},
    install_requires=[
        "pandas>=1.5.0",
        "openpyxl>=3.0.0",
        "requests>=2.28.0",
        "pyyaml>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "company-quickcheck=company_quickcheck.cli:main",
        ],
    },
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries",
        "Topic :: Utilities",
    ],
)
