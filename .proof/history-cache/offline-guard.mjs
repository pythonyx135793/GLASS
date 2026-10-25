globalThis.fetch = async () => { throw new Error('Offline test attempted network access'); };
globalThis.WebSocket = class { constructor() { throw new Error('Offline test attempted websocket access'); } };
