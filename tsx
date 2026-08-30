// add near the other useState lines:
const [accessOpen, setAccessOpen] = useState(false);
// replace the header button:
<Button onClick={() => setAccessOpen(true)}>Request access →</Button>
// and on the access modal: <Modal open={accessOpen} onClose={() => setAccessOpen(false)} …>