# Bash completion for Mocap Studio.
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
#
# grammar top-commands: help update uninstall completion
# grammar top-options: -h --help --port --no-browser --reuse-existing --data-dir --version
# grammar help-topics: update uninstall completion
# grammar update-options: -h --help --launch --repo --release-base-url --tag-prefix --no-desktop-integration
# grammar uninstall-options: -h --help --yes
# grammar completion-values: install bash zsh fish

_mocap_studio_complete_words() {
    local ms_current=$1
    local ms_words=$2
    local ms_candidate

    while IFS= read -r ms_candidate; do
        COMPREPLY[${#COMPREPLY[@]}]=$ms_candidate
    done < <(compgen -W "$ms_words" -- "$ms_current")
}

_mocap_studio_complete_directories() {
    local ms_current=$1
    local ms_prefix=${2-}
    local ms_candidate

    while IFS= read -r ms_candidate; do
        COMPREPLY[${#COMPREPLY[@]}]=$ms_prefix$ms_candidate
    done < <(compgen -d -- "$ms_current")
}

_mocap_studio_complete() {
    local ms_current=${COMP_WORDS[COMP_CWORD]}
    local ms_previous=
    local ms_command=
    local ms_word
    local ms_index=1

    COMPREPLY=()
    if (( COMP_CWORD > 0 )); then
        ms_previous=${COMP_WORDS[COMP_CWORD - 1]}
    fi

    # Find the first command while skipping values belonging to launch options.
    # COMP_WORDS[0] is deliberately ignored so an absolute/relative command path
    # completes in exactly the same way as the bare command name.
    while (( ms_index < COMP_CWORD )); do
        ms_word=${COMP_WORDS[ms_index]}
        case $ms_word in
            --port|--data-dir)
                (( ms_index += 2 ))
                continue
                ;;
            --port=*|--data-dir=*)
                (( ms_index += 1 ))
                continue
                ;;
            help|update|uninstall|completion)
                ms_command=$ms_word
                break
                ;;
        esac
        (( ms_index += 1 ))
    done

    case $ms_command:$ms_previous in
        :--data-dir)
            _mocap_studio_complete_directories "$ms_current"
            return
            ;;
        :--port|update:--repo|update:--release-base-url|update:--tag-prefix)
            # These values are free-form; returning no candidates prevents the
            # option list from being offered as the value.
            return
            ;;
    esac

    if [[ -z $ms_command ]]; then
        case $ms_current in
            --data-dir=*)
                _mocap_studio_complete_directories \
                    "${ms_current#--data-dir=}" "--data-dir="
                return
                ;;
            --port=*) return ;;
        esac
        _mocap_studio_complete_words "$ms_current" \
            "help update uninstall completion -h --help --port --no-browser --reuse-existing --data-dir --version"
        return
    fi

    case $ms_command in
        help)
            _mocap_studio_complete_words "$ms_current" \
                "update uninstall completion"
            ;;
        update)
            _mocap_studio_complete_words "$ms_current" \
                "-h --help --launch --repo --release-base-url --tag-prefix --no-desktop-integration"
            ;;
        uninstall)
            _mocap_studio_complete_words "$ms_current" \
                "-h --help --yes"
            ;;
        completion)
            _mocap_studio_complete_words "$ms_current" "install bash zsh fish"
            ;;
    esac
}

complete -F _mocap_studio_complete mocap-studio
