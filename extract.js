const fs = require('fs');
const readline = require('readline');

async function processFile(file, outStream) {
  if (!fs.existsSync(file)) {
      outStream.write(`\n=== File not found: ${file} ===\n`);
      return;
  }
  const fileStream = fs.createReadStream(file);
  const rl = readline.createInterface({
    input: fileStream,
    crlfDelay: Infinity
  });
  outStream.write(`\n=== Conversation: ${file} ===\n`);
  for await (const line of rl) {
    try {
      const obj = JSON.parse(line);
      if (obj.source === 'USER_EXPLICIT' || obj.type === 'USER_INPUT') {
        outStream.write(`\n--- USER ---\n${obj.content}\n`);
      } else if (obj.source === 'MODEL' && obj.type === 'PLANNER_RESPONSE') {
        outStream.write(`\n--- MODEL ---\n${obj.content}\n`);
      }
    } catch (e) {}
  }
}

async function main() {
  const outPath = 'E:\\My_AI_Projects\\archviz-biz-manager\\extracted_logs.txt';
  const outStream = fs.createWriteStream(outPath);
  await processFile('C:\\Users\\xiao_\\.gemini\\antigravity\\brain\\010e697b-08e8-4db8-b23f-341a22cf9ab6\\.system_generated\\logs\\transcript.jsonl', outStream);
  await processFile('C:\\Users\\xiao_\\.gemini\\antigravity\\brain\\af01618a-eeee-46f8-ba8b-929162236a8a\\.system_generated\\logs\\transcript.jsonl', outStream);
  outStream.end();
  console.log('Extraction complete.');
}
main();
