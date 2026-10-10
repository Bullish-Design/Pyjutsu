{
  description = "Pyjutsu: a Pythonic binding to jujutsu's jj-lib engine, built with maturin.";

  # The development environment is driven by `devenv.yaml`. It is not restated as a
  # `devShells` output. This flake carries the package, the overlay, and the checks only.
  #
  # Outputs:
  #   packages.default / packages.pyjutsu   the `pyjutsu` command and the `pyjutsu` module
  #   packages.pyjutsu-lib                  the Python package, for other Python packages
  #   overlays.default                      adds `pyjutsu` to pythonPackagesExtensions, so a
  #                                         consumer builds against its own interpreter
  #   checks.pyjutsu                        the package build and its smoke command
  #
  # The wheel on the GitHub release stays the route for uv. This flake is the route for Nix.
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/e7439b6b14ad3cc35d05608ebca9bce01a25f5f8";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      # `requires-python` is ">=3.13". The abi3 extension targets 3.13. Name the interpreter.
      pythonSetFor = pkgs: pkgs.python313Packages;
      packageFor = pkgs: pkgs.callPackage ./nix/package.nix {
        python3Packages = pythonSetFor pkgs;
      };
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pythonSetFor pkgs;
          pyjutsu-lib = packageFor pkgs;
          pyjutsu = python.toPythonApplication pyjutsu-lib;
        in
        {
          inherit pyjutsu-lib pyjutsu;
          default = pyjutsu;
        });

      overlays.default = final: prev: {
        pythonPackagesExtensions = (prev.pythonPackagesExtensions or [ ]) ++ [
          (pyFinal: _pyPrev: {
            pyjutsu = final.callPackage ./nix/package.nix { python3Packages = pyFinal; };
          })
        ];
      };

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.pyjutsu}/bin/pyjutsu";
        };
      });

      checks = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          # The package build is the check: installCheckPhase runs the smoke command.
          pyjutsu = packageFor pkgs;
        });
    };
}
