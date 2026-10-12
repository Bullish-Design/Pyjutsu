# Pinned native jj for the development shell. nixpkgs lags: it lacks jj 0.46.0, which adds
# `workspace add --colocate`, which gitman needs. Replace this file with `pkgs.jujutsu` once
# nixpkgs ships 0.46 or later. The consumer module (modules/copyroom.nix) keeps `pkgs.jujutsu`.
{ pkgs }:

let
  version = "0.46.0";
  assets = {
    x86_64-linux = {
      target = "x86_64-unknown-linux-musl";
      hash = "sha256-/OAnEVjmZc64LcZsXrlbFyhkkJTpEzB087W67ONF/Ag=";
    };
    aarch64-linux = {
      target = "aarch64-unknown-linux-musl";
      hash = "sha256-gOKPdQHBHlDgygbNah+h2YLP+cxZv+RBGp02qlAdhn4=";
    };
  };
  asset = assets.${pkgs.stdenv.hostPlatform.system};
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "jujutsu-bin";
  inherit version;
  src = pkgs.fetchurl {
    url = "https://github.com/jj-vcs/jj/releases/download/v${version}/jj-v${version}-${asset.target}.tar.gz";
    inherit (asset) hash;
  };
  sourceRoot = ".";
  installPhase = ''
    install -Dm755 jj $out/bin/jj
  '';
}
