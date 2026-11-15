/** Static discovery aid for the Phase 0 public-contract audit; not a complete inventory. */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';

function callerName(node) {
	for (let current = node.parent; current; current = current.parent) {
		if (ts.isFunctionDeclaration(current) || ts.isMethodDeclaration(current)) {
			return current.name?.getText() ?? '<anonymous>';
		}
		if ((ts.isArrowFunction(current) || ts.isFunctionExpression(current)) &&
			current.parent && ts.isVariableDeclaration(current.parent)) {
			return current.parent.name.getText();
		}
	}
	return '<top-level>';
}

function requestMethod(args) {
	const input = args[0];
	const inputIsString = ts.isStringLiteral(input) || ts.isNoSubstitutionTemplateLiteral(input) || ts.isTemplateExpression(input);
	if (args.length < 2) return inputIsString ? 'GET' : 'UNKNOWN';
	const options = args[1];
	if (!ts.isObjectLiteralExpression(options)) return 'UNKNOWN';
	let method = inputIsString ? 'GET' : 'UNKNOWN';
	for (const property of options.properties) {
		if (ts.isSpreadAssignment(property)) {
			method = 'UNKNOWN';
		} else if (property.name) {
			let name;
			if (ts.isComputedPropertyName(property.name)) {
				if (!ts.isStringLiteral(property.name.expression)) {
					method = 'UNKNOWN';
					continue;
				}
				name = property.name.expression.text;
			} else {
				name = property.name.getText().replace(/^['"]|['"]$/g, '');
			}
			if (name === 'method') {
				method = ts.isPropertyAssignment(property) && ts.isStringLiteral(property.initializer)
					? property.initializer.text.toUpperCase() : 'UNKNOWN';
			}
		}
	}
	return method;
}

export function scanSource(source, file) {
	const scripts = [];
	const parse_errors = [];
	if (file.endsWith('.svelte')) {
		const regex = /<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi;
		for (const match of source.matchAll(regex)) {
			const start = match.index + match[0].indexOf('>') + 1;
			scripts.push({ text: match[1], lineOffset: source.slice(0, start).split('\n').length - 1 });
		}
		if ((source.match(/<script\b/gi) ?? []).length !== scripts.length) {
			parse_errors.push({ file, line: 1, message: 'Unmatched script tag; inspect this component manually.' });
		}
	} else {
		scripts.push({ text: source, lineOffset: 0 });
	}
	const entries = [];
	const wrappers = [];
	for (const script of scripts) {
		const ast = ts.createSourceFile(file, script.text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
		for (const diagnostic of ast.parseDiagnostics) {
			const line = ast.getLineAndCharacterOfPosition(diagnostic.start ?? 0).line + 1 + script.lineOffset;
			parse_errors.push({ file, line, message: ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ') });
		}
		function visit(node) {
			if (ts.isCallExpression(node)) {
				const expression = node.expression;
				const directFetch = ts.isIdentifier(expression) && expression.text === 'fetch';
				const memberFetch = ts.isPropertyAccessExpression(expression) && expression.name.text === 'fetch';
				const wrapper = ts.isIdentifier(expression) && expression.text === 'api';
				if ((directFetch || memberFetch || wrapper) && node.arguments.length) {
					const line = ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1 + script.lineOffset;
					const common = { file, line, caller: callerName(node), url_expression: node.arguments[0].getText(ast) };
					if (wrapper) wrappers.push({ ...common, callee: 'api' });
					else entries.push({ ...common, callee: expression.getText(ast), method: requestMethod(node.arguments) });
				}
			}
			ts.forEachChild(node, visit);
		}
		visit(ast);
	}
	return { entries, wrappers, parse_errors };
}

function sourceFiles(root) {
	const result = [];
	function visit(directory) {
		for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
			const absolute = path.join(directory, entry.name);
			if (entry.isDirectory()) visit(absolute);
			else if (entry.isFile() && /\.(ts|js|svelte)$/.test(entry.name) &&
				!/\.(test|spec|d)\.(ts|js)$/.test(entry.name)) result.push(absolute);
		}
	}
	visit(root);
	return result;
}

export function inventory(root) {
	const src = path.join(root, 'src');
	const all = { source_root: 'packages/frontend/src', entries: [], wrappers: [], parse_errors: [] };
	for (const absolute of sourceFiles(src)) {
		const relative = path.relative(root, absolute).replaceAll(path.sep, '/');
		const result = scanSource(fs.readFileSync(absolute, 'utf8'), relative);
		all.entries.push(...result.entries);
		all.wrappers.push(...result.wrappers);
		all.parse_errors.push(...result.parse_errors);
	}
	return all;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
	const root = path.resolve(path.dirname(process.argv[1]), '..');
	process.stdout.write(JSON.stringify(inventory(root), null, 2) + '\n');
}
