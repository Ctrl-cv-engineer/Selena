import { create } from 'zustand';
import { Message, DebugInfo, CollectionData, ScheduleData } from '../types';

interface AppState {
  // Chat State
  messages: Message[];
  addMessage: (message: Message) => void;
  isTyping: boolean;
  setIsTyping: (isTyping: boolean) => void;

  // Debug State
  debugInfo: DebugInfo;
  addDebugMessage: (type: 'agent' | 'rolePlay' | 'simple', message: Message) => void;
  clearDebugMessages: () => void;

  // Data State
  collections: string[];
  setCollections: (collections: string[]) => void;
  currentCollectionData: CollectionData | null;
  setCurrentCollectionData: (data: CollectionData | null) => void;

  // Schedule State
  scheduleData: Record<string, ScheduleData>;
  setScheduleData: (date: string, data: ScheduleData) => void;

  // Config State
  config: string;
  setConfig: (config: string) => void;
}

export const useStore = create<AppState>((set) => ({
  messages: [],
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  isTyping: false,
  setIsTyping: (isTyping) => set({ isTyping }),

  debugInfo: {
    agentMessages: [],
    rolePlayMessages: [],
    simpleMessages: [],
  },
  addDebugMessage: (type, message) =>
    set((state) => {
      const key = `${type}Messages` as keyof DebugInfo;
      return {
        debugInfo: {
          ...state.debugInfo,
          [key]: [...state.debugInfo[key], message],
        },
      };
    }),
  clearDebugMessages: () =>
    set({
      debugInfo: {
        agentMessages: [],
        rolePlayMessages: [],
        simpleMessages: [],
      },
    }),

  collections: [],
  setCollections: (collections) => set({ collections }),
  currentCollectionData: null,
  setCurrentCollectionData: (data) => set({ currentCollectionData: data }),

  scheduleData: {},
  setScheduleData: (date, data) =>
    set((state) => ({
      scheduleData: { ...state.scheduleData, [date]: data },
    })),

  config: '{}',
  setConfig: (config) => set({ config }),
}));
