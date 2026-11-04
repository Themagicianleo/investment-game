// The single in-browser API instance the UI talks to.
import { LocalApi, fetchLoader } from './api';
import { browserKV } from './storage';

export const localApi = new LocalApi(fetchLoader(import.meta.env.BASE_URL), browserKV());
