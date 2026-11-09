import { describe, expect, it } from 'vitest';
import { buildChatRequestToolRouting } from './chatRequestToolRouting';

const createInput = (overrides: Record<string, any> = {}) => ({
  selectedToolIds: [
    'browser',
    'server:internal',
    'direct_server:2',
    'direct_server:terminal-x',
    'direct_server:internal',
  ],
  selectedSkillIds: ['skill-a'],
  selectedFilterIds: ['filter-a'],
  enableFunctions: true,
  enableToolServers: true,
  enableTools: true,
  terminalCapability: true,
  requestTerminalId: 'terminal-url',
  filesystemAttachment: null,
  toolServers: [
    { id: 'internal' },
    { id: 'other' },
    { id: 'third' },
    { id: 'terminal-x' },
  ],
  terminalServers: [
    { url: 'terminal-url' },
    { id: 'named-terminal', url: 'named-url' },
  ],
  ...overrides,
});

describe('buildChatRequestToolRouting', () => {
  it('partitions direct server selections and builds enabled routes', () => {
    const input = createInput();
    const routing = buildChatRequestToolRouting(input);

    expect(routing).toEqual({
      toolIds: ['browser', 'server:internal'],
      filterIds: ['filter-a'],
      skillIds: ['skill-a'],
      terminalId: 'terminal-url',
      toolServers: [
        { id: 'internal' },
        { id: 'third' },
        { id: 'terminal-x' },
        { url: 'terminal-url', is_terminal: true },
      ],
    });
    expect(routing.skillIds).not.toBe(input.selectedSkillIds);
  });

  it('filters tools and filters according to feature flags', () => {
    const routing = buildChatRequestToolRouting(
      createInput({ enableFunctions: false, enableToolServers: false }),
    );

    expect(routing.toolIds).toEqual(['browser']);
    expect(routing.filterIds).toBeUndefined();
    expect(routing.skillIds).toEqual(['skill-a']);
    expect(routing.terminalId).toBeUndefined();
    expect(routing.toolServers).toEqual([]);
  });

  it('omits terminal access when the model lacks terminal capability', () => {
    const routing = buildChatRequestToolRouting(
      createInput({ terminalCapability: false }),
    );

    expect(routing.terminalId).toBeUndefined();
    expect(routing.toolServers).toEqual([
      { id: 'internal' },
      { id: 'third' },
      { id: 'terminal-x' },
    ]);
  });

  it('limits direct terminal-server injection to an attached filesystem terminal', () => {
    const routing = buildChatRequestToolRouting(
      createInput({
        requestTerminalId: 'filesystem-terminal',
        filesystemAttachment: { terminal_id: 'filesystem-terminal' },
        terminalServers: [
          { url: 'filesystem-terminal' },
          { url: 'other-terminal' },
          { id: 'named-terminal', url: 'filesystem-terminal' },
        ],
      }),
    );

    expect(routing.toolServers).toEqual([
      { id: 'internal' },
      { id: 'third' },
      { id: 'terminal-x' },
      { url: 'filesystem-terminal', is_terminal: true },
    ]);
  });
});
