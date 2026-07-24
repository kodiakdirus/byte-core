#!/bin/sh

if [ "${BYTE_CORE_SHELL_LOADED:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi
BYTE_CORE_SHELL_LOADED=1
export BYTE_CORE_SHELL_LOADED

byte_status() {
    printf '%s\n' "Byte shell integration is active."
}

byte_repo() {
    if [ "$#" -ne 1 ]; then
        printf '%s\n' "usage: byte_repo PATH" >&2
        return 2
    fi
    if [ ! -d "$1" ]; then
        printf '%s\n' "byte_repo: directory not found" >&2
        return 1
    fi
    command cd -- "$1" || return
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        printf '%s\n' "byte_repo: not a Git worktree" >&2
        return 1
    fi
}
