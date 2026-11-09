export type ChatRequestToolServer = {
  id?: string | number;
  url?: string;
  [key: string]: any;
};

export type ChatRequestToolRoutingDependencies = {
  selectedToolIds: string[];
  selectedSkillIds: string[];
  selectedFilterIds: string[];
  enableFunctions?: boolean;
  enableToolServers?: boolean;
  enableTools?: boolean;
  terminalCapability?: boolean | null;
  requestTerminalId?: string | null;
  filesystemAttachment?: { terminal_id?: string | null } | null;
  toolServers?: ChatRequestToolServer[] | null;
  terminalServers?: ChatRequestToolServer[] | null;
};

/** Resolves selected functions, skills and terminal server routes for one chat request. */
export const buildChatRequestToolRouting = ({
  selectedToolIds,
  selectedSkillIds,
  selectedFilterIds,
  enableFunctions,
  enableToolServers,
  enableTools,
  terminalCapability,
  requestTerminalId,
  filesystemAttachment,
  toolServers,
  terminalServers,
}: ChatRequestToolRoutingDependencies) => {
  const toolIds: string[] = [];
  const toolServerIds: Array<string | number> = [];

  for (const toolId of selectedToolIds) {
    if (toolId.startsWith('direct_server:')) {
      const serverId = toolId.replace('direct_server:', '');
      const numericServerId = parseInt(serverId);
      toolServerIds.push(!isNaN(numericServerId) ? numericServerId : serverId);
    } else {
      toolIds.push(toolId);
    }
  }

  const terminalEnabled = Boolean(
    enableToolServers && (terminalCapability ?? true),
  );
  const resolvedToolServers = enableToolServers
    ? [
        ...(toolServers ?? []).filter(
          (server, index) =>
            toolServerIds.includes(index) || toolServerIds.includes(server?.id),
        ),
        ...(terminalEnabled
          ? (terminalServers ?? [])
              .filter(
                (server) =>
                  !server.id &&
                  (!filesystemAttachment || server.url === requestTerminalId),
              )
              .map((server) => ({ ...server, is_terminal: true }))
          : []),
      ]
    : [];

  return {
    toolIds: toolIds.filter((id) =>
      id.startsWith('server:') ? enableToolServers : enableTools,
    ),
    filterIds:
      enableFunctions && selectedFilterIds.length > 0
        ? selectedFilterIds
        : undefined,
    skillIds: selectedSkillIds.length > 0 ? [...selectedSkillIds] : undefined,
    terminalId:
      terminalEnabled && requestTerminalId ? requestTerminalId : undefined,
    toolServers: resolvedToolServers,
  };
};
