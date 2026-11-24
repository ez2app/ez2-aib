#!/bin/bash
# aib-knives: os=unix
# ORDER-autodrive-webdev 7ebca 3 knives RT1-RT2 (7ebca 11-20): the tool list steers review work
# into the thread (seat_send is private; thread_send is where review goes, the person @s in).
set -eo pipefail
rt() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "$3"; }
# RT1 seat_send no longer says review goes in the thread
rt "work on a tree item (handing it over for review, a review, a result) goes in that item's thread with thread_send." \
   "use it for anything." the_tools_say_review_goes_in_the_thread
# RT2 thread_send no longer says review is posted here
rt "Hand work over for review and post a review here, not with seat_send, so the person and ask_person see it. " \
   "Post freely. " the_tools_say_review_goes_in_the_thread
