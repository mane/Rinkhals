declare module 'qrcode' {
	interface CanvasOptions {
		margin?: number;
		width?: number;
		color?: { dark?: string; light?: string };
		errorCorrectionLevel?: 'L' | 'M' | 'Q' | 'H';
	}
	const QRCode: {
		toCanvas(canvas: HTMLCanvasElement, text: string, options?: CanvasOptions): Promise<void>;
	};
	export default QRCode;
}
