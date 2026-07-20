# Fish completion for Mocap Studio.
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
#
# grammar top-commands: help update uninstall completion
# grammar top-options: -h --help --port --no-browser --reuse-existing --data-dir --version
# grammar help-topics: update uninstall completion
# grammar update-options: -h --help --launch --repo --release-base-url --tag-prefix --no-desktop-integration
# grammar uninstall-options: -h --help --yes
# grammar completion-values: install bash zsh fish

function __mocap_studio_command
    set -l tokens (commandline -opc)
    set -l index 2

    # tokens[1] may be a command path, so only inspect its arguments.
    while test $index -le (count $tokens)
        set -l token $tokens[$index]
        switch $token
            case --port --data-dir
                set index (math $index + 2)
                continue
            case '--port=*' '--data-dir=*'
                set index (math $index + 1)
                continue
            case help update uninstall completion
                echo $token
                return 0
        end
        set index (math $index + 1)
    end
end

function __mocap_studio_no_command
    set -l command (__mocap_studio_command)
    test -z "$command"
end

function __mocap_studio_using_command
    set -l command (__mocap_studio_command)
    test "$command" = "$argv[1]"
end

complete -c mocap-studio -n __mocap_studio_no_command -f \
    -a help -d 'Show help for a management command'
complete -c mocap-studio -n __mocap_studio_no_command -f \
    -a update -d 'Update to the latest release'
complete -c mocap-studio -n __mocap_studio_no_command -f \
    -a uninstall -d 'Remove managed application files'
complete -c mocap-studio -n __mocap_studio_no_command -f \
    -a completion -d 'Print shell completion code'
complete -c mocap-studio -n __mocap_studio_no_command -s h -l help \
    -d 'Show help'
complete -c mocap-studio -n __mocap_studio_no_command -l port -x \
    -d 'Use a loopback HTTP port'
complete -c mocap-studio -n __mocap_studio_no_command -l no-browser \
    -d 'Do not open the browser'
complete -c mocap-studio -n __mocap_studio_no_command -l reuse-existing \
    -d 'Reopen an existing instance'
complete -c mocap-studio -n __mocap_studio_no_command -l data-dir -r \
    -a '(__fish_complete_directories)' -d 'Override the take library directory'
complete -c mocap-studio -n __mocap_studio_no_command -l version \
    -d 'Show the installed version'

complete -c mocap-studio -n '__mocap_studio_using_command help' -f \
    -a update -d 'Show update command help'
complete -c mocap-studio -n '__mocap_studio_using_command help' -f \
    -a uninstall -d 'Show uninstall command help'
complete -c mocap-studio -n '__mocap_studio_using_command help' -f \
    -a completion -d 'Show completion command help'

complete -c mocap-studio -n '__mocap_studio_using_command update' -s h -l help \
    -d 'Show update help'
complete -c mocap-studio -n '__mocap_studio_using_command update' -l launch \
    -d 'Launch after updating'
complete -c mocap-studio -n '__mocap_studio_using_command update' -l repo -x \
    -d 'Use a GitHub OWNER/REPO'
complete -c mocap-studio -n '__mocap_studio_using_command update' \
    -l release-base-url -x -d 'Use a release download root'
complete -c mocap-studio -n '__mocap_studio_using_command update' \
    -l tag-prefix -x -d 'Use a release tag prefix'
complete -c mocap-studio -n '__mocap_studio_using_command update' \
    -l no-desktop-integration -d 'Do not install graphical integration'

complete -c mocap-studio -n '__mocap_studio_using_command uninstall' -s h -l help \
    -d 'Show uninstall help'
complete -c mocap-studio -n '__mocap_studio_using_command uninstall' -l yes \
    -d 'Confirm uninstall non-interactively'

complete -c mocap-studio -n '__mocap_studio_using_command completion' -f \
    -a install -d 'Install completion for the current user'
complete -c mocap-studio -n '__mocap_studio_using_command completion' -f \
    -a 'bash zsh fish' -d 'Shell whose completion code should be printed'
