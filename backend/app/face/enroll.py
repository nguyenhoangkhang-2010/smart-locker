"""Enrol a user's face from photos on disk.

    python -m app.face.enroll --user 1 photo1.jpg photo2.jpg photo3.jpg
    python -m app.face.enroll --user 1 --clear      # forget this user's face

Use 3-5 photos: front, slightly left/right, with and without glasses, and
in the lighting the camera will really see. Each photo must show one face
(if several are visible the largest is used and you get a warning).
"""
import argparse
import sys
from pathlib import Path

from . import store
from .engine import NoFace, decode, engine


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--user", type=int, required=True, help="users.id")
    parser.add_argument("--clear", action="store_true", help="delete enrolled faces")
    parser.add_argument("photos", nargs="*", type=Path)
    args = parser.parse_args()

    if args.clear:
        print(f"removed {store.clear_embeddings(args.user)} enrolled photo(s)")
        return
    if not args.photos:
        parser.error("give at least one photo, or use --clear")

    total = None
    for path in args.photos:
        try:
            vector, faces = engine.embed(decode(path.read_bytes()))
        except NoFace:
            print(f"SKIP {path.name}: no face found")
            continue
        except (OSError, ValueError) as exc:
            print(f"SKIP {path.name}: {exc}")
            continue

        if faces > 1:
            print(f"WARN {path.name}: {faces} faces, used the largest")
        total = store.add_embedding(args.user, vector)
        print(f"ok   {path.name}")

    if total is None:
        sys.exit("nothing enrolled")
    print(f"user {args.user} now has {total} enrolled photo(s)")


if __name__ == "__main__":
    main()
