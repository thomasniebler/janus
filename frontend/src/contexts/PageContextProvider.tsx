import React, { useState } from 'react';

import { createPageContextStore, PageContextStoreContext } from './pageContext';

/** Holds the tab's page-context registry (JAN-250); mounted around `ChatSessionProvider`. */
export const PageContextProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [store] = useState(createPageContextStore);
  return <PageContextStoreContext.Provider value={store}>{children}</PageContextStoreContext.Provider>;
};
