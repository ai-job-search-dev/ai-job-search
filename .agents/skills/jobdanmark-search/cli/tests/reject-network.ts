globalThis.fetch = Object.assign(async () => {
  throw new Error("unexpected network request for an invalid detail URL")
}, { preconnect() {} })
