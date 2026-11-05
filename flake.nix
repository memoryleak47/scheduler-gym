{
  description = "scheduler-gym dev shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    fenix = {
      url = "github:nix-community/fenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      fenix,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };

        toolchain = fenix.packages.${system}.stable.withComponents [
          "cargo"
          "rustc"
          "rust-src"
          "rustfmt"
          "clippy"
          "rust-analyzer"
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          # lean-egg: libegg_for_lean.so calls back into Lean-compiled code
          # (`lean_is_synthable`) and libffi.so calls into libegg_for_lean.so.
          # This cycle only resolves with lazy binding, so `-z now` must be off.
          hardeningDisable = [ "bindnow" ];

          nativeBuildInputs = [
            toolchain
          ]
          ++ (with pkgs; [
            gnumake
            cargo-nextest
            nil
            nixfmt
            ruff
            ty
            uv
            # lean-egg case
            elan
            # benchmarking: taskset (bench.py pins case studies), monitoring tools
            util-linux
            linuxPackages.turbostat
            lm_sensors
          ]);

          # szalinski's default make target converts inputs with openscad.
          # (Its mesh-diff targets also need an old CGAL; not provided.)
          buildInputs = with pkgs; [
            openscad
          ];
        };
      }
    );
}
