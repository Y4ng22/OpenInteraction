import { baseEnvironment, clean, processAcpConnection } from './shared.mjs'

export const deepSeekHarnessBackendDriver = {
  id: 'deepseek',
  label: 'DeepSeek',
  capabilities: {
    delegation: false,
    permissions: true,
    backendUi: false,
    nativeSessionHistory: false,
    externalMcp: true,
    nativeDelegation: false,
    sessionMcp: true,
    coordinatorMcpInstructions: false,
  },

  createProfile({ directory, cliPath }) {
    return {
      label: this.label,
      acpConnection: processAcpConnection({
        command: clean(cliPath) || 'dsh',
        args: ['--profile', 'acp'],
        cwd: directory,
        env: baseEnvironment('deepseek'),
      }),
      // The official CLI owns ACP initialization and the user's profile.
      externalMcp: true,
      sessionMcp: true,
      nativeDelegation: false,
      delegation: false,
      nativeSessionHistory: false,
      backendUi: false,
      sessionInstructions: [
        'You serve a live voice conversation. Be brief and finish promptly.',
        'For a simple current-information query, use at most one web_search and answer from its result when sufficient.',
        'Do not repeat searches or inspect unrelated pages to chase minor details.',
        'Do not claim to have opened a separate Gateway-managed task Session.',
      ].join(' '),
    }
  },
}
