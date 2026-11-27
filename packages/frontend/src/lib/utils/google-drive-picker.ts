import { WEAI_API_BASE_URL, WEAI_BASE_URL } from '$lib/constants';

export interface PickedDriveFile {
	id: string;
	name: string;
	mimeType: string;
	url: string;
}

const DEFAULT_MIME_TYPES =
	'application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.google-apps.document,application/vnd.google-apps.spreadsheet,application/vnd.google-apps.presentation';

// Google Drive Picker API configuration
let API_KEY = '';
let CLIENT_ID = '';

// Function to fetch credentials from backend config
async function getCredentials() {
	const response = await fetch(`${WEAI_BASE_URL}/api/config`, { credentials: 'include' });
	if (!response.ok) {
		throw new Error('Failed to fetch Google Drive credentials');
	}
	const config = await response.json();
	API_KEY = config.google_drive?.api_key;
	CLIENT_ID = config.google_drive?.client_id;

	if (!API_KEY || !CLIENT_ID) {
		throw new Error('Google Drive API credentials not configured');
	}
}
const SCOPE = ['https://www.googleapis.com/auth/drive.file'];

// Validate required credentials
const validateCredentials = () => {
	if (!API_KEY || !CLIENT_ID) {
		throw new Error('Google Drive API credentials not configured');
	}
	if (API_KEY === '' || CLIENT_ID === '') {
		throw new Error('Please configure valid Google Drive API credentials');
	}
};

let pickerApiLoaded = false;
let oauthToken: string | null = null;
let initialized = false;

export const loadGoogleDriveApi = () => {
	return new Promise((resolve, reject) => {
		if (typeof gapi === 'undefined') {
			const script = document.createElement('script');
			script.src = 'https://apis.google.com/js/api.js';
			script.onload = () => {
				gapi.load('picker', () => {
					pickerApiLoaded = true;
					resolve(true);
				});
			};
			script.onerror = reject;
			document.body.appendChild(script);
		} else {
			gapi.load('picker', () => {
				pickerApiLoaded = true;
				resolve(true);
			});
		}
	});
};

export const loadGoogleAuthApi = () => {
	return new Promise((resolve, reject) => {
		if (typeof google === 'undefined') {
			const script = document.createElement('script');
			script.src = 'https://accounts.google.com/gsi/client';
			script.onload = resolve;
			script.onerror = reject;
			document.body.appendChild(script);
		} else {
			resolve(true);
		}
	});
};

export const getAuthToken = async (): Promise<string> => {
	if (!oauthToken) {
		return new Promise<string>((resolve, reject) => {
			const tokenClient = google.accounts.oauth2.initTokenClient({
				client_id: CLIENT_ID,
				scope: SCOPE.join(' '),
				callback: (response: any) => {
					if (response.access_token) {
						oauthToken = response.access_token as string;
						resolve(oauthToken);
					} else {
						reject(new Error('Failed to get access token'));
					}
				},
				error_callback: (error: any) => {
					reject(new Error(error.message || 'OAuth error occurred'));
				}
			});
			tokenClient.requestAccessToken();
		});
	}
	return oauthToken;
};

const initialize = async () => {
	if (!initialized) {
		await getCredentials();
		validateCredentials();
		await Promise.all([loadGoogleDriveApi(), loadGoogleAuthApi()]);
		initialized = true;
	}
};

const resolvePickerToken = async (explicitToken?: string | null): Promise<string> => {
	if (explicitToken) {
		return explicitToken;
	}
	if (typeof localStorage !== 'undefined' && localStorage.token) {
		try {
			const res = await fetch(`${WEAI_API_BASE_URL}/agent-runtime/drive/picker-token`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (res.ok) {
				const data = await res.json();
				if (data?.access_token) {
					return data.access_token;
				}
			}
		} catch {
			// Fall back to GIS browser token client.
		}
	}
	const token = await getAuthToken();
	if (!token) {
		throw new Error('Unable to get OAuth token');
	}
	return token;
};

const openPickerSession = async (options?: {
	oauthToken?: string | null;
	multiselect?: boolean;
	mimeTypes?: string;
}): Promise<{ token: string; docs: any[] } | null> => {
	await initialize();
	const token = await resolvePickerToken(options?.oauthToken);

	return new Promise((resolve, reject) => {
		try {
			const builder = new google.picker.PickerBuilder()
				.enableFeature(google.picker.Feature.NAV_HIDDEN);
			if (options?.multiselect !== false) {
				builder.enableFeature(google.picker.Feature.MULTISELECT_ENABLED);
			}
			const picker = builder
				.addView(
					new google.picker.DocsView()
						.setMode(google.picker.DocsViewMode.LIST)
						.setIncludeFolders(false)
						.setSelectFolderEnabled(false)
						.setMimeTypes(options?.mimeTypes ?? DEFAULT_MIME_TYPES)
				)
				.setOAuthToken(token)
				.setDeveloperKey(API_KEY)
				// drive.file only grants access to picked files when the picker knows the project number
				.setAppId(CLIENT_ID.split('-')[0])
				.setOrigin(window.location.origin)
				.setCallback((data: any) => {
					if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {
						resolve({
							token,
							docs: data[google.picker.Response.DOCUMENTS] ?? []
						});
					} else if (data[google.picker.Response.ACTION] === google.picker.Action.CANCEL) {
						resolve(null);
					}
				})
				.build();
			picker.setVisible(true);
		} catch (error) {
			reject(error);
		}
	});
};

export const pickDriveFiles = async (options?: {
	oauthToken?: string | null;
	multiselect?: boolean;
	mimeTypes?: string;
}): Promise<PickedDriveFile[] | null> => {
	const session = await openPickerSession(options);
	if (!session || session.docs.length === 0) {
		return null;
	}
	return session.docs.map((doc: any) => ({
		id: doc[google.picker.Document.ID],
		name: doc[google.picker.Document.NAME],
		mimeType: doc[google.picker.Document.MIME_TYPE] ?? '',
		url: doc[google.picker.Document.URL] ?? ''
	}));
};

export const createPicker = async () => {
	try {
		const session = await openPickerSession({ multiselect: true });
		if (!session || session.docs.length === 0) {
			return null;
		}
		const { token, docs } = session;
		const doc = docs[0];
		const fileId = doc[google.picker.Document.ID];
		const fileName = doc[google.picker.Document.NAME];

		if (!fileId || !fileName) {
			throw new Error('Required file details missing');
		}

		const mimeType = doc[google.picker.Document.MIME_TYPE];
		let downloadUrl: string;
		if (mimeType.includes('google-apps')) {
			let exportFormat = 'application/pdf';
			if (mimeType.includes('document')) {
				exportFormat = 'text/plain';
			} else if (mimeType.includes('spreadsheet')) {
				exportFormat = 'text/csv';
			} else if (mimeType.includes('presentation')) {
				exportFormat = 'text/plain';
			}
			downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=${encodeURIComponent(exportFormat)}`;
		} else {
			downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
		}

		const response = await fetch(downloadUrl, {
			headers: {
				Authorization: `Bearer ${token}`,
				Accept: '*/*'
			}
		});
		if (!response.ok) {
			const errorText = await response.text();
			throw new Error(`Failed to download file (${response.status}): ${errorText}`);
		}

		return {
			id: fileId,
			name: fileName,
			url: downloadUrl,
			blob: await response.blob(),
			headers: {
				Authorization: `Bearer ${token}`,
				Accept: '*/*'
			}
		};
	} catch (error) {
		console.error('Google Drive Picker error:', error);
		throw error;
	}
};
